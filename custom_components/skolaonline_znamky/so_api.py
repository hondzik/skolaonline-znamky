"""Async klient pro API Škola OnLine (Libre) — bez závislosti na `homeassistant.*`.

Testovatelné samostatně (`pytest tests/test_so_api.py`, HTTP mockované ručně přes
`_FakeResponse`) a spustitelné jako CLI proti reálnému účtu:

    SO_USER=... SO_PASS=... python so_api.py user
    SO_USER=... SO_PASS=... python so_api.py semesters --student-id <id>
    SO_USER=... SO_PASS=... python so_api.py marks --student-id <id> [--semester-id <id>]

CLI slouží k živému ověření chování API — především chování `SemesterId`/`schoolYearId`
bez zadání a stránkování u `marks/list`.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://aplikace.skolaonline.cz/solapi/api"
CLIENT_ID = "test_client"
SCOPE = "openid offline_access profile sol_api"
TOKEN_REFRESH_MARGIN = 60  # sekund před expirací, kdy se token obnovuje


class SkolaOnlineError(Exception):
    """Obecná chyba komunikace s API (spojení, neočekávaná odpověď)."""


class SkolaOnlineAuthError(SkolaOnlineError):
    """Neplatné přihlašovací údaje nebo token, který nejde obnovit."""


@dataclasses.dataclass
class Student:
    id: str
    name: str


@dataclasses.dataclass
class Semester:
    id: str
    name: str
    date_from: str
    date_to: str


@dataclasses.dataclass
class Mark:
    id: str
    subject_id: str
    value: str
    weight: float
    date: str
    theme: str
    verbal_evaluation: str
    is_points: bool


@dataclasses.dataclass
class MarksList:
    marks: list[Mark]
    subject_names: dict[str, str]


class SkolaOnlineClient:
    """Drží OAuth2 token a volá REST API. Token žije jen v paměti instance.

    Refresh token se zkouší jako první cesta k obnově (`grant_type=refresh_
    token`); pokud selže (expirovaný/zneplatněný), padá se zpět na nové
    přihlášení uloženým heslem — password grant to umožňuje bez dalšího
    zásahu uživatele, na rozdíl od authorization-code OAuth flow.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str = BASE_URL) -> None:
        self._session = session
        self._base_url = base_url
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._username: str | None = None
        self._password: str | None = None
        self._lock = asyncio.Lock()

    async def async_login(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        await self._async_fetch_token(
            {
                "grant_type": "password",
                "username": username,
                "password": password,
                "client_id": CLIENT_ID,
                "scope": SCOPE,
            }
        )

    async def _async_fetch_token(self, data: dict[str, str]) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}/connect/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status in (400, 401):
                    raise SkolaOnlineAuthError(f"Přihlášení selhalo (HTTP {resp.status})")
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise SkolaOnlineError(f"Chyba spojení při přihlašování: {err}") from err

        self._access_token = payload["access_token"]
        self._refresh_token = payload.get("refresh_token", self._refresh_token)
        self._expires_at = time.monotonic() + float(payload.get("expires_in", 300))

    async def _async_ensure_token(self) -> None:
        if self._access_token and time.monotonic() < self._expires_at - TOKEN_REFRESH_MARGIN:
            return
        async with self._lock:
            # Znovu zkontrolovat po získání zámku — souběžná úloha mohla
            # token mezitím už obnovit, ať se neobnovuje zbytečně dvakrát.
            if self._access_token and time.monotonic() < self._expires_at - TOKEN_REFRESH_MARGIN:
                return
            if self._refresh_token:
                try:
                    await self._async_fetch_token(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": self._refresh_token,
                            "client_id": CLIENT_ID,
                        }
                    )
                    return
                except SkolaOnlineError:
                    _LOGGER.debug("Obnova tokenu selhala, zkouší se nové přihlášení heslem")
            if not self._username or not self._password:
                raise SkolaOnlineAuthError("Chybí uložené přihlašovací údaje pro obnovu tokenu")
            await self._async_fetch_token(
                {
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                    "client_id": CLIENT_ID,
                    "scope": SCOPE,
                }
            )

    async def _async_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._async_ensure_token()
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            ) as resp:
                if resp.status == 401:
                    raise SkolaOnlineAuthError(f"Neplatný token (HTTP 401) na {path}")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise SkolaOnlineError(f"Chyba spojení na {path}: {err}") from err

    async def async_get_user(self) -> dict[str, Any]:
        """Vrátí syrovou odpověď `/v1/user`."""
        return await self._async_get("/v1/user")

    async def async_list_students(self) -> list[Student]:
        """Odvodí seznam dětí z `/v1/user`.

        `children` (`UserInfoResponse.children: UserInfoChild[]`, pole `id`/
        `displayName`) je oficiálně zdokumentované v `swagger.json` přímo
        z API (`.../solapi/swagger/v1/swagger.json`). Pokud pole přesto chybí
        (starší instalace apod.), vrátí se aspoň přihlášený účet samotný
        (žákovský login) — `config_flow.py` na tomhle staví fallback na
        ruční zadání studentId.
        """
        user = await self.async_get_user()
        children = user.get("children")
        if children:
            return [
                Student(
                    id=str(c["id"]),
                    name=c.get("displayName") or c.get("firstName") or str(c["id"]),
                )
                for c in children
            ]
        if user.get("personID"):
            return [
                Student(
                    id=str(user["personID"]),
                    name=user.get("fullName") or str(user["personID"]),
                )
            ]
        return []

    async def async_get_semesters(self, student_id: str) -> list[Semester]:
        data = await self._async_get(
            "/v1/timeTable/codeLists", params={"studentId": student_id}
        )
        return [
            Semester(
                id=str(s["id"]),
                name=s.get("name", ""),
                date_from=s.get("dateFrom", ""),
                date_to=s.get("dateTo", ""),
            )
            for s in data.get("semester", [])
        ]

    async def async_get_marks(
        self, student_id: str, semester_id: str | None = None
    ) -> MarksList:
        params: dict[str, Any] = {"SigningFilter": "all"}
        if semester_id:
            params["SemesterId"] = semester_id
        data = await self._async_get(
            f"/v1/students/{student_id}/marks/list", params=params
        )
        marks = [
            Mark(
                id=str(m["id"]),
                subject_id=str(m["subjectId"]),
                value=m.get("markText", ""),
                weight=float(m.get("weight") or 0.0),
                date=m.get("markDate", ""),
                theme=m.get("theme", ""),
                verbal_evaluation=m.get("verbalEvaluation", ""),
                is_points=bool(m.get("isPoints", False)),
            )
            for m in data.get("marks", [])
        ]
        subject_names = {str(s["id"]): s.get("name", "") for s in data.get("subjects", [])}
        return MarksList(marks=marks, subject_names=subject_names)

    async def async_get_timetable_subjects(
        self, student_id: str, date_from: str, date_to: str
    ) -> dict[str, str]:
        """Vrátí `{subjectId: name}` z rozvrhu za dané období (`/v1/timeTable`).

        Na rozdíl od `async_get_marks` nezávisí na existenci hodnocení — zachytí
        i předměty, kde zatím žádná známka nepadla (`marks/list` takové předměty
        vůbec neobsahuje).
        """
        data = await self._async_get(
            "/v1/timeTable",
            params={"StudentId": student_id, "DateFrom": date_from, "DateTo": date_to},
        )
        subjects: dict[str, str] = {}
        for day in data.get("days", []):
            for scheduled in day.get("schedules", []):
                subject = scheduled.get("subject")
                if subject and subject.get("id"):
                    subjects[str(subject["id"])] = subject.get("name", "")
        return subjects


def weighted_average(marks: list[Mark]) -> float | None:
    """Vážený průměr klasických známek (bodové/slovní hodnocení se vynechává).

    Bodové (`is_points=True`) a slovní hodnocení nejsou na stejné škále jako
    klasifikace 1-5, takže se do průměru nepočítají. `markText` s modifikátorem
    (`"2-"`, `"1+"`) se bere jako základní číslo bez modifikátoru — hrubá
    aproximace, přesnější zpracování by vyžadovalo živě ověřit, jaké všechny
    tvary `markText` API vrací. Váha `<=0`/chybějící se bere jako 1.0 (stejný
    fallback jako `bakalari-ha`).
    """
    total_weight = 0.0
    total = 0.0
    for m in marks:
        if m.is_points:
            continue
        try:
            value = float(m.value.strip().rstrip("+-").replace(",", "."))
        except (ValueError, AttributeError):
            continue
        weight = m.weight if m.weight and m.weight > 0 else 1.0
        total += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return total / total_weight


def _asdict_all(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, list):
        return [_asdict_all(v) for v in value]
    return value


async def _async_main(args: argparse.Namespace) -> None:
    username = os.environ.get("SO_USER")
    password = os.environ.get("SO_PASS")
    if not username or not password:
        raise SystemExit("Nastav proměnné prostředí SO_USER a SO_PASS.")

    async with aiohttp.ClientSession() as session:
        client = SkolaOnlineClient(session)
        await client.async_login(username, password)

        if args.command == "user":
            result: Any = await client.async_get_user()
        elif args.command == "students":
            result = _asdict_all(await client.async_list_students())
        elif args.command == "semesters":
            result = _asdict_all(await client.async_get_semesters(args.student_id))
        elif args.command == "marks":
            marks_list = await client.async_get_marks(args.student_id, args.semester_id)
            result = {
                "marks": _asdict_all(marks_list.marks),
                "subject_names": marks_list.subject_names,
                "weighted_average": weighted_average(marks_list.marks),
            }
        else:
            raise SystemExit(f"Neznámý příkaz: {args.command}")

        print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI klient Škola OnLine API")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("user")
    sub.add_parser("students")
    p_sem = sub.add_parser("semesters")
    p_sem.add_argument("--student-id", required=True)
    p_marks = sub.add_parser("marks")
    p_marks.add_argument("--student-id", required=True)
    p_marks.add_argument("--semester-id", default=None)

    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
