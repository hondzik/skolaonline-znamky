"""Coordinator pro Škola OnLine — stahuje známky a agreguje je pro sensor entity.

Pro každé nakonfigurované dítě zjistí aktuální pololetí, stáhne jeho známky,
spočítá vážené průměry po předmětech i celkově a porovná nové ID známek s
`Store` (persistuje mezi restarty HA), aby šlo bezpečně vystřelit event jen
pro doopravdy nové známky — bez toho by po každém restartu HA integrace
nahlásila jako "nové" všechny známky znovu.

`unique_id` sensor entit (`sensor.py`) neobsahuje `semester_id` — entita je
tedy stabilní napříč pololetími/roky, jen si "pod sebou" mění, na jaké
pololetí `state`/atributy odkazují (viz CLAUDE.md a diskuze v zadání).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import random

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import so_api
from .const import (
    CONF_MARKS_PER_SUBJECT,
    CONF_STUDENTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    DEFAULT_UPDATE_MINUTES,
    DOMAIN,
    EVENT_NEW_MARK,
    MARKS_PER_SUBJECT,
    SEMESTER_REFRESH_HOURS,
    STORE_KEY_TEMPLATE,
    STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class SubjectMarks:
    subject_id: str
    subject_name: str
    average: float | None
    count: int
    marks: list[so_api.Mark]  # jen posledních MARKS_PER_SUBJECT, nejnovější první


@dataclasses.dataclass
class StudentData:
    student_id: str
    student_name: str
    semester_id: str | None
    semester_name: str
    semester_from: str
    semester_to: str
    school_year: str
    previous_semester_id: str | None
    marks_count: int
    average: float | None
    subjects: list[SubjectMarks]


def _parse_date(value: str) -> dt.date | None:
    """Zkusí rozparsovat datum z API — přesný formát NEOVĚŘEN proti spec (jen `string`)."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _school_year_label(date_from: dt.date | None) -> str:
    if date_from is None:
        return ""
    start_year = date_from.year if date_from.month >= 8 else date_from.year - 1
    return f"{start_year}/{start_year + 1}"


def _pick_current_semester(
    semesters: list[so_api.Semester], today: dt.date
) -> tuple[so_api.Semester | None, so_api.Semester | None]:
    """Vybere pololetí, do jehož rozsahu spadá `today`, a to bezprostředně předchozí.

    Řadí podle `date_from`. Pokud žádné pololetí `today` neobsahuje (např. o
    prázdninách mezi roky), bere se poslední, jehož `date_from` už nastal;
    když není ani to, vrací se úplně první ze seznamu (nejstarší dostupná
    data spíš než nic).
    """
    if not semesters:
        return None, None

    parsed = sorted(
        semesters,
        key=lambda s: _parse_date(s.date_from) or dt.date.min,
    )

    current_idx = None
    for idx, sem in enumerate(parsed):
        start = _parse_date(sem.date_from)
        end = _parse_date(sem.date_to)
        if start and end and start <= today <= end:
            current_idx = idx
            break

    if current_idx is None:
        for idx in range(len(parsed) - 1, -1, -1):
            start = _parse_date(parsed[idx].date_from)
            if start and start <= today:
                current_idx = idx
                break

    if current_idx is None:
        current_idx = 0

    current = parsed[current_idx]
    previous = parsed[current_idx - 1] if current_idx > 0 else None
    return current, previous


class SkolaOnlineCoordinator(DataUpdateCoordinator[dict[str, StudentData]]):
    """Stahuje známky všech nakonfigurovaných dětí jednoho rodičovského účtu."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval_minutes = entry.options.get(
            CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_MINUTES
        )
        interval_seconds = interval_minutes * 60
        jitter = random.uniform(-0.1, 0.1) * interval_seconds
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=dt.timedelta(seconds=interval_seconds + jitter),
        )
        self.entry = entry
        self.hub_device_id: str | None = None
        self._marks_per_subject = entry.options.get(CONF_MARKS_PER_SUBJECT, MARKS_PER_SUBJECT)
        self.client = so_api.SkolaOnlineClient(aiohttp_client.async_get_clientsession(hass))
        self._store: Store = Store(
            hass, STORE_VERSION, STORE_KEY_TEMPLATE.format(entry_id=entry.entry_id)
        )
        self._seen_marks: dict[str, dict[str, list[str]]] = {}
        self._semesters_cache: dict[str, tuple[dt.datetime, list[so_api.Semester]]] = {}

    async def _async_setup(self) -> None:
        try:
            await self.client.async_login(
                self.entry.data["username"], self.entry.data["password"]
            )
        except so_api.SkolaOnlineAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except so_api.SkolaOnlineError as err:
            raise UpdateFailed(str(err)) from err

        stored = await self._store.async_load()
        self._seen_marks = stored or {}

    async def _async_update_data(self) -> dict[str, StudentData]:
        students: dict[str, str] = self.entry.options.get(
            CONF_STUDENTS, self.entry.data.get(CONF_STUDENTS, {})
        )
        result: dict[str, StudentData] = {}
        store_dirty = False

        for student_id, student_name in students.items():
            try:
                semesters = await self._async_get_semesters_cached(student_id)
                current, previous = _pick_current_semester(semesters, dt.date.today())
                if current is None:
                    result[student_id] = StudentData(
                        student_id=student_id,
                        student_name=student_name,
                        semester_id=None,
                        semester_name="",
                        semester_from="",
                        semester_to="",
                        school_year="",
                        previous_semester_id=None,
                        marks_count=0,
                        average=None,
                        subjects=[],
                    )
                    continue

                marks_list = await self.client.async_get_marks(student_id, current.id)
            except so_api.SkolaOnlineAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except so_api.SkolaOnlineError as err:
                raise UpdateFailed(f"{student_name}: {err}") from err

            previous_id = previous.id if previous else None
            if self._async_process_new_marks(
                student_id, current.id, previous_id, marks_list, student_name
            ):
                store_dirty = True

            result[student_id] = self._build_student_data(
                student_id, student_name, current, previous, marks_list
            )

        if store_dirty:
            self._store.async_delay_save(lambda: self._seen_marks, 10)

        return result

    async def _async_get_semesters_cached(self, student_id: str) -> list[so_api.Semester]:
        cached = self._semesters_cache.get(student_id)
        if cached is not None:
            fetched_at, semesters = cached
            age = dt.datetime.now() - fetched_at
            current, _ = _pick_current_semester(semesters, dt.date.today())
            still_valid_period = current is not None and (
                (end := _parse_date(current.date_to)) is None or end >= dt.date.today()
            )
            if age < dt.timedelta(hours=SEMESTER_REFRESH_HOURS) and still_valid_period:
                return semesters

        semesters = await self.client.async_get_semesters(student_id)
        self._semesters_cache[student_id] = (dt.datetime.now(), semesters)
        return semesters

    def _async_process_new_marks(
        self,
        student_id: str,
        semester_id: str,
        previous_semester_id: str | None,
        marks_list: so_api.MarksList,
        student_name: str,
    ) -> bool:
        """Porovná známky s naposledy viděnými, vystřelí event pro nové. Vrací True při změně."""
        student_seen = self._seen_marks.setdefault(student_id, {})
        is_first_time = semester_id not in student_seen
        previously_seen_ids = set(student_seen.get(semester_id, []))
        current_ids = {m.id for m in marks_list.marks}

        if not is_first_time:
            new_ids = current_ids - previously_seen_ids
            for mark in marks_list.marks:
                if mark.id not in new_ids:
                    continue
                self.hass.bus.async_fire(
                    EVENT_NEW_MARK,
                    {
                        "student_id": student_id,
                        "student_name": student_name,
                        "semester_id": semester_id,
                        "subject_id": mark.subject_id,
                        "subject_name": marks_list.subject_names.get(mark.subject_id, ""),
                        "mark_id": mark.id,
                        "value": mark.value,
                        "weight": mark.weight,
                        "date": mark.date,
                        "theme": mark.theme,
                    },
                )

        # Drží se jen aktuální + bezprostředně předchozí pololetí na dítě
        # (to předchozí se nepřepočítává, jen se ponechá beze změny) — jinak
        # by úložiště rostlo donekonečna s každým dalším pololetím.
        new_entry = {semester_id: sorted(current_ids)}
        if previous_semester_id and previous_semester_id in student_seen:
            new_entry[previous_semester_id] = student_seen[previous_semester_id]
        self._seen_marks[student_id] = new_entry

        if not is_first_time and previously_seen_ids != current_ids:
            return True
        return is_first_time

    def _build_student_data(
        self,
        student_id: str,
        student_name: str,
        current: so_api.Semester,
        previous: so_api.Semester | None,
        marks_list: so_api.MarksList,
    ) -> StudentData:
        by_subject: dict[str, list[so_api.Mark]] = {}
        for mark in marks_list.marks:
            by_subject.setdefault(mark.subject_id, []).append(mark)

        subjects: list[SubjectMarks] = []
        for subject_id, marks in by_subject.items():
            marks_sorted = sorted(marks, key=lambda m: m.date, reverse=True)
            subjects.append(
                SubjectMarks(
                    subject_id=subject_id,
                    subject_name=marks_list.subject_names.get(subject_id, subject_id),
                    average=so_api.weighted_average(marks),
                    count=len(marks),
                    marks=marks_sorted[: self._marks_per_subject],
                )
            )
        subjects.sort(key=lambda s: s.subject_name)

        subject_averages = [s.average for s in subjects if s.average is not None]
        # Celkový průměr = průměr průměrů jednotlivých předmětů (stejně jako
        # na vysvědčení), NE vážený průměr přes všechny známky napříč
        # předměty — to by zvýhodňovalo předměty s víc zápisy.
        overall_average = (
            round(sum(subject_averages) / len(subject_averages), 2)
            if subject_averages
            else None
        )

        return StudentData(
            student_id=student_id,
            student_name=student_name,
            semester_id=current.id,
            semester_name=current.name,
            semester_from=current.date_from,
            semester_to=current.date_to,
            school_year=_school_year_label(_parse_date(current.date_from)),
            previous_semester_id=previous.id if previous else None,
            marks_count=len(marks_list.marks),
            average=overall_average,
            subjects=subjects,
        )

    async def async_fetch_marks(
        self, student_id: str, semester_id: str | None, subject_id: str | None
    ) -> dict:
        """Čerstvě z API stáhne kompletní známky (pro službu `get_marks` — "více" v kartě)."""
        try:
            marks_list = await self.client.async_get_marks(student_id, semester_id)
        except so_api.SkolaOnlineAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except so_api.SkolaOnlineError as err:
            raise UpdateFailed(str(err)) from err

        marks = marks_list.marks
        if subject_id:
            marks = [m for m in marks if m.subject_id == subject_id]

        return {
            "marks": [
                {
                    "id": m.id,
                    "subject_id": m.subject_id,
                    "subject_name": marks_list.subject_names.get(m.subject_id, ""),
                    "value": m.value,
                    "weight": m.weight,
                    "date": m.date,
                    "theme": m.theme,
                    "verbal_evaluation": m.verbal_evaluation,
                    "is_points": m.is_points,
                }
                for m in marks
            ],
        }
