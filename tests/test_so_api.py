"""Testy pro so_api.py — čistý async Python, žádná závislost na Home Assistantu.

`so_api` se importuje jako samostatný top-level modul (ne přes balíček
`custom_components.skolaonline_znamky`) — viz `pyproject.toml`
(`tool.pytest.ini_options.pythonpath`).

HTTP se mockuje ručně (`_FakeResponse` + patch `aiohttp.ClientSession.post`/
`.get`), ne přes `aioresponses` — ta ve verzi 0.7.9 (poslední na PyPI) neumí
`aiohttp` v aktuální verzi vyžadované Home Assistantem (`ClientResponse.
__init__` tam přibylo povinné `stream_writer`), takže by testy spadly na
chybu knihovny, ne kódu. Vlastní fake nezávisí na vnitřním rozhraní
`aiohttp` a je tedy vůči podobným změnám odolnější.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import aiohttp
import pytest

import so_api

TOKEN_URL = f"{so_api.BASE_URL}/connect/token"
USER_URL = f"{so_api.BASE_URL}/v1/user"


class _FakeResponse:
    """Minimální náhrada `aiohttp.ClientResponse` použitelná jako `async with`."""

    def __init__(self, status: int = 200, json_body: dict | None = None, raise_exc: Exception | None = None):
        self.status = status
        self._json_body = json_body if json_body is not None else {}
        self._raise_exc = raise_exc

    async def __aenter__(self) -> "_FakeResponse":
        if self._raise_exc:
            raise self._raise_exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=self.status
            )

    async def json(self, content_type=None) -> dict:
        return self._json_body


def _patch_post(*responses: _FakeResponse):
    return patch.object(aiohttp.ClientSession, "post", MagicMock(side_effect=list(responses)))


def _patch_get(*responses: _FakeResponse):
    return patch.object(aiohttp.ClientSession, "get", MagicMock(side_effect=list(responses)))


def _token_response(access_token: str = "token-1", expires_in: int = 300) -> _FakeResponse:
    return _FakeResponse(
        json_body={
            "access_token": access_token,
            "refresh_token": "refresh-1",
            "expires_in": expires_in,
        }
    )


# ---------------------------------------------------------------------------
# login / token refresh
# ---------------------------------------------------------------------------


async def test_login_success_stores_tokens():
    with _patch_post(_token_response()):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")

    assert client._access_token == "token-1"
    assert client._refresh_token == "refresh-1"


async def test_login_invalid_credentials_raises_auth_error():
    with _patch_post(_FakeResponse(status=400)):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            with pytest.raises(so_api.SkolaOnlineAuthError):
                await client.async_login("user", "spatne")


async def test_login_connection_error_raises_skolaonline_error():
    with _patch_post(_FakeResponse(raise_exc=aiohttp.ClientConnectionError("boom"))):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            with pytest.raises(so_api.SkolaOnlineError):
                await client.async_login("user", "pass")


async def test_ensure_token_refreshes_before_expiry_using_refresh_token():
    post_mock = MagicMock(
        side_effect=[_token_response(expires_in=0), _token_response(access_token="token-2")]
    )
    with patch.object(aiohttp.ClientSession, "post", post_mock), _patch_get(
        _FakeResponse(json_body={"personID": "1", "fullName": "Test"})
    ):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            await client.async_get_user()

    assert client._access_token == "token-2"
    assert post_mock.call_count == 2
    refresh_call = post_mock.call_args_list[1]
    assert refresh_call.kwargs["data"]["grant_type"] == "refresh_token"
    assert refresh_call.kwargs["data"]["refresh_token"] == "refresh-1"


async def test_ensure_token_falls_back_to_password_when_refresh_fails():
    post_mock = MagicMock(
        side_effect=[
            _token_response(expires_in=0),  # login
            _FakeResponse(status=400),  # pokus o refresh selže
            _token_response(access_token="token-3"),  # fallback na heslo
        ]
    )
    with patch.object(aiohttp.ClientSession, "post", post_mock), _patch_get(
        _FakeResponse(json_body={"personID": "1", "fullName": "Test"})
    ):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            await client.async_get_user()

    assert client._access_token == "token-3"
    grant_types = [c.kwargs["data"]["grant_type"] for c in post_mock.call_args_list]
    assert grant_types == ["password", "refresh_token", "password"]


async def test_ensure_token_raises_when_no_stored_credentials_for_fallback():
    """Simuluje stav, kdy token nikdy nebyl získán a refresh token chybí."""
    async with aiohttp.ClientSession() as session:
        client = so_api.SkolaOnlineClient(session)
        with pytest.raises(so_api.SkolaOnlineAuthError):
            await client._async_ensure_token()


async def test_get_raises_auth_error_on_401():
    with _patch_post(_token_response()), _patch_get(_FakeResponse(status=401)):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            with pytest.raises(so_api.SkolaOnlineAuthError):
                await client.async_get_user()


async def test_get_wraps_connection_error():
    with _patch_post(_token_response()), _patch_get(
        _FakeResponse(raise_exc=aiohttp.ClientConnectionError("boom"))
    ):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            with pytest.raises(so_api.SkolaOnlineError):
                await client.async_get_user()


# ---------------------------------------------------------------------------
# async_list_students
# ---------------------------------------------------------------------------


async def test_list_students_uses_children_field_when_present():
    user_payload = {
        "personID": "parent-1",
        "fullName": "Rodič",
        "children": [
            {"id": "1", "displayName": "Anna"},
            {"id": "2", "firstName": "Petr"},
        ],
    }
    with _patch_post(_token_response()), _patch_get(_FakeResponse(json_body=user_payload)):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            students = await client.async_list_students()

    assert students == [
        so_api.Student(id="1", name="Anna"),
        so_api.Student(id="2", name="Petr"),
    ]


async def test_list_students_falls_back_to_logged_in_user_without_children():
    with _patch_post(_token_response()), _patch_get(
        _FakeResponse(json_body={"personID": "42", "fullName": "Žák Sám"})
    ):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            students = await client.async_list_students()

    assert students == [so_api.Student(id="42", name="Žák Sám")]


async def test_list_students_empty_when_no_personid_and_no_children():
    with _patch_post(_token_response()), _patch_get(_FakeResponse(json_body={})):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            students = await client.async_list_students()

    assert students == []


# ---------------------------------------------------------------------------
# async_get_semesters
# ---------------------------------------------------------------------------


async def test_get_semesters_parses_response():
    payload = {
        "semester": [
            {
                "id": "s1",
                "name": "1. pololetí",
                "dateFrom": "2026-09-01",
                "dateTo": "2027-01-31",
            }
        ]
    }
    with _patch_post(_token_response()), _patch_get(_FakeResponse(json_body=payload)):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            semesters = await client.async_get_semesters("student-1")

    assert semesters == [
        so_api.Semester(
            id="s1", name="1. pololetí", date_from="2026-09-01", date_to="2027-01-31"
        )
    ]


# ---------------------------------------------------------------------------
# async_get_marks
# ---------------------------------------------------------------------------


def _marks_payload() -> dict:
    return {
        "marks": [
            {
                "id": "m1",
                "subjectId": "math",
                "markText": "1",
                "weight": 2.0,
                "markDate": "2026-09-10T00:00:00",
                "theme": "Zlomky",
                "verbalEvaluation": "",
                "isPoints": False,
            },
            {
                "id": "m2",
                "subjectId": "math",
                "markText": "85",
                "weight": 1.0,
                "markDate": "2026-09-15T00:00:00",
                "theme": "Test",
                "verbalEvaluation": "",
                "isPoints": True,
            },
        ],
        "subjects": [{"id": "math", "name": "Matematika"}],
    }


async def test_get_marks_parses_marks_and_subjects():
    with _patch_post(_token_response()), _patch_get(_FakeResponse(json_body=_marks_payload())):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            marks_list = await client.async_get_marks("student-1", "sem-1")

    assert len(marks_list.marks) == 2
    assert marks_list.marks[0].id == "m1"
    assert marks_list.marks[0].date == "2026-09-10T00:00:00"
    assert marks_list.marks[1].is_points is True
    assert marks_list.subject_names == {"math": "Matematika"}


async def test_get_marks_sends_semester_id_when_given():
    get_mock = MagicMock(return_value=_FakeResponse(json_body=_marks_payload()))
    with _patch_post(_token_response()), patch.object(aiohttp.ClientSession, "get", get_mock):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            await client.async_get_marks("student-1", "sem-1")

    assert get_mock.call_args.kwargs["params"] == {"SigningFilter": "all", "SemesterId": "sem-1"}


async def test_get_marks_omits_semester_id_when_not_given():
    get_mock = MagicMock(return_value=_FakeResponse(json_body=_marks_payload()))
    with _patch_post(_token_response()), patch.object(aiohttp.ClientSession, "get", get_mock):
        async with aiohttp.ClientSession() as session:
            client = so_api.SkolaOnlineClient(session)
            await client.async_login("user", "pass")
            await client.async_get_marks("student-1", None)

    assert get_mock.call_args.kwargs["params"] == {"SigningFilter": "all"}


# ---------------------------------------------------------------------------
# weighted_average
# ---------------------------------------------------------------------------


def _mark(value: str, weight: float = 1.0, is_points: bool = False) -> so_api.Mark:
    return so_api.Mark(
        id="x",
        subject_id="math",
        value=value,
        weight=weight,
        date="2026-09-01",
        theme="",
        verbal_evaluation="",
        is_points=is_points,
    )


def test_weighted_average_basic():
    marks = [_mark("1", weight=2.0), _mark("3", weight=1.0)]
    # (1*2 + 3*1) / 3 = 5/3
    assert so_api.weighted_average(marks) == pytest.approx(5 / 3)


def test_weighted_average_excludes_points_marks():
    marks = [_mark("1", weight=1.0), _mark("100", weight=1.0, is_points=True)]
    assert so_api.weighted_average(marks) == 1.0


def test_weighted_average_strips_modifier_suffix():
    marks = [_mark("2-"), _mark("2+")]
    assert so_api.weighted_average(marks) == 2.0


def test_weighted_average_missing_or_nonpositive_weight_defaults_to_one():
    marks = [_mark("2", weight=0.0), _mark("4", weight=-1.0)]
    assert so_api.weighted_average(marks) == 3.0


def test_weighted_average_empty_list_returns_none():
    assert so_api.weighted_average([]) is None


def test_weighted_average_only_points_marks_returns_none():
    assert so_api.weighted_average([_mark("100", is_points=True)]) is None


def test_weighted_average_unparseable_value_is_skipped():
    marks = [_mark("N/A"), _mark("2")]
    assert so_api.weighted_average(marks) == 2.0
