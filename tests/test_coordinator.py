"""Testy pro coordinator.py: výběr pololetí, diff nových známek přes `Store`,
agregaci pro `StudentData` a cache pololetí.

`SkolaOnlineClient` je všude mockovaný (`AsyncMock`) — komunikace se API se
testuje samostatně v `test_so_api.py`, tady se testuje jen logika kolem ní.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.skolaonline_znamky import so_api
from custom_components.skolaonline_znamky.const import CONF_STUDENTS, DOMAIN, EVENT_NEW_MARK
from custom_components.skolaonline_znamky.coordinator import (
    SkolaOnlineCoordinator,
    _parse_date,
    _pick_current_semester,
    _school_year_label,
    _timetable_window,
)

USERNAME = "rodic@example.cz"
PASSWORD = "heslo123"
STUDENT_ID = "student-1"
STUDENT_NAME = "Anna"


def _entry_data() -> dict:
    return {
        "username": USERNAME,
        "password": PASSWORD,
        CONF_STUDENTS: {STUDENT_ID: STUDENT_NAME},
    }


def _make_coordinator(hass) -> tuple[SkolaOnlineCoordinator, MockConfigEntry]:
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data())
    entry.add_to_hass(hass)
    coordinator = SkolaOnlineCoordinator(hass, entry)
    coordinator.client = AsyncMock(spec=so_api.SkolaOnlineClient)
    # Výchozí "žádné doplňkové předměty z rozvrhu" — testy, které se o tuhle
    # obohacovací funkci nezajímají, si tak nemusí mock samy konfigurovat.
    coordinator.client.async_get_timetable_subjects = AsyncMock(return_value={})
    return coordinator, entry


def _semester(id_: str, date_from: str, date_to: str, name: str = "") -> so_api.Semester:
    return so_api.Semester(id=id_, name=name or id_, date_from=date_from, date_to=date_to)


def _mark(
    id_: str, subject_id: str = "math", value: str = "1", weight: float = 1.0, date: str = "2026-09-10"
) -> so_api.Mark:
    return so_api.Mark(
        id=id_,
        subject_id=subject_id,
        value=value,
        weight=weight,
        date=date,
        theme="",
        verbal_evaluation="",
        is_points=False,
    )


# ---------------------------------------------------------------------------
# _parse_date / _school_year_label (čisté funkce)
# ---------------------------------------------------------------------------


def test_parse_date_valid_iso_date():
    assert _parse_date("2026-09-14") == dt.date(2026, 9, 14)


def test_parse_date_valid_iso_datetime_prefix():
    assert _parse_date("2026-09-14T08:00:00") == dt.date(2026, 9, 14)


def test_parse_date_invalid_returns_none():
    assert _parse_date("not-a-date") is None


def test_parse_date_empty_returns_none():
    assert _parse_date("") is None


def test_school_year_label_from_august_is_current_calendar_year():
    assert _school_year_label(dt.date(2026, 9, 1)) == "2026/2027"


def test_school_year_label_before_august_is_previous_calendar_year():
    assert _school_year_label(dt.date(2027, 1, 15)) == "2026/2027"


def test_school_year_label_none_date_returns_empty_string():
    assert _school_year_label(None) == ""


# ---------------------------------------------------------------------------
# _pick_current_semester
# ---------------------------------------------------------------------------


def test_pick_current_semester_picks_semester_containing_today():
    semesters = [
        _semester("s1", "2026-09-01", "2027-01-31"),
        _semester("s2", "2027-02-01", "2027-06-30"),
    ]
    current, previous = _pick_current_semester(semesters, dt.date(2027, 3, 1))
    assert current.id == "s2"
    assert previous.id == "s1"


def test_pick_current_semester_falls_back_to_latest_started_when_none_contains_today():
    semesters = [
        _semester("s1", "2026-09-01", "2027-01-31"),
        _semester("s2", "2027-02-01", "2027-06-30"),
    ]
    # léto mezi pololetími — today je po konci s2, ale žádné pololetí ho neobsahuje
    current, previous = _pick_current_semester(semesters, dt.date(2027, 8, 1))
    assert current.id == "s2"
    assert previous.id == "s1"


def test_pick_current_semester_falls_back_to_first_when_none_started_yet():
    semesters = [_semester("s1", "2026-09-01", "2027-01-31")]
    current, previous = _pick_current_semester(semesters, dt.date(2020, 1, 1))
    assert current.id == "s1"
    assert previous is None


def test_pick_current_semester_empty_list_returns_none():
    assert _pick_current_semester([], dt.date.today()) == (None, None)


def test_pick_current_semester_first_semester_has_no_previous():
    semesters = [_semester("s1", "2026-09-01", "2027-01-31")]
    current, previous = _pick_current_semester(semesters, dt.date(2026, 10, 1))
    assert current.id == "s1"
    assert previous is None


# ---------------------------------------------------------------------------
# _timetable_window
# ---------------------------------------------------------------------------


def test_timetable_window_returns_14_day_range_from_semester_start():
    window = _timetable_window("2026-09-01")
    assert window == ("2026-09-01T00:00:00", "2026-09-15T00:00:00")


def test_timetable_window_unparseable_date_returns_none():
    assert _timetable_window("") is None
    assert _timetable_window("not-a-date") is None


# ---------------------------------------------------------------------------
# _async_process_new_marks
# ---------------------------------------------------------------------------


async def test_process_new_marks_first_sighting_does_not_fire_event(hass):
    coordinator, _entry = _make_coordinator(hass)
    events = async_capture_events(hass, EVENT_NEW_MARK)
    marks_list = so_api.MarksList(marks=[_mark("m1")], subject_names={"math": "Matematika"})

    changed = coordinator._async_process_new_marks(
        STUDENT_ID, "sem-1", None, marks_list, STUDENT_NAME
    )

    assert changed is True
    assert events == []
    assert coordinator._seen_marks[STUDENT_ID]["sem-1"] == ["m1"]


async def test_process_new_marks_fires_event_for_genuinely_new_mark(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator._seen_marks = {STUDENT_ID: {"sem-1": ["m1"]}}
    events = async_capture_events(hass, EVENT_NEW_MARK)
    marks_list = so_api.MarksList(
        marks=[_mark("m1"), _mark("m2", value="2")],
        subject_names={"math": "Matematika"},
    )

    changed = coordinator._async_process_new_marks(
        STUDENT_ID, "sem-1", None, marks_list, STUDENT_NAME
    )

    assert changed is True
    assert len(events) == 1
    assert events[0].data["mark_id"] == "m2"
    assert events[0].data["student_id"] == STUDENT_ID
    assert events[0].data["subject_name"] == "Matematika"


async def test_process_new_marks_no_change_returns_false(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator._seen_marks = {STUDENT_ID: {"sem-1": ["m1"]}}
    events = async_capture_events(hass, EVENT_NEW_MARK)
    marks_list = so_api.MarksList(marks=[_mark("m1")], subject_names={})

    changed = coordinator._async_process_new_marks(
        STUDENT_ID, "sem-1", None, marks_list, STUDENT_NAME
    )

    assert changed is False
    assert events == []


async def test_process_new_marks_preserves_previous_semester_entry(hass):
    """Předchozí pololetí se má v `Store` zachovat beze změny, ne zahodit —
    jinak by `get_marks` service po dalším refreshi nemělo z čeho porovnávat."""
    coordinator, _entry = _make_coordinator(hass)
    coordinator._seen_marks = {
        STUDENT_ID: {"sem-0": ["old-1"], "sem-1": ["m1"]},
    }
    marks_list = so_api.MarksList(marks=[_mark("m1"), _mark("m2")], subject_names={})

    coordinator._async_process_new_marks(STUDENT_ID, "sem-1", "sem-0", marks_list, STUDENT_NAME)

    assert coordinator._seen_marks[STUDENT_ID]["sem-0"] == ["old-1"]
    assert set(coordinator._seen_marks[STUDENT_ID]["sem-1"]) == {"m1", "m2"}


async def test_process_new_marks_drops_semesters_other_than_current_and_previous(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator._seen_marks = {
        STUDENT_ID: {"sem-ancient": ["ancient-1"], "sem-0": ["old-1"], "sem-1": ["m1"]},
    }
    marks_list = so_api.MarksList(marks=[_mark("m1")], subject_names={})

    coordinator._async_process_new_marks(STUDENT_ID, "sem-1", "sem-0", marks_list, STUDENT_NAME)

    assert set(coordinator._seen_marks[STUDENT_ID].keys()) == {"sem-0", "sem-1"}


# ---------------------------------------------------------------------------
# _build_student_data
# ---------------------------------------------------------------------------


async def test_build_student_data_overall_average_is_mean_of_subject_averages(hass):
    """Vědomě NE vážený průměr napříč všemi známkami — averagem se rozumí průměr průměrů předmětů."""
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(
        marks=[
            _mark("m1", subject_id="math", value="1", weight=1.0),
            _mark("m2", subject_id="math", value="1", weight=1.0),
            _mark("m3", subject_id="math", value="1", weight=1.0),
            _mark("m4", subject_id="cz", value="5", weight=1.0),
        ],
        subject_names={"math": "Matematika", "cz": "Čeština"},
    )
    current = _semester("sem-1", "2026-09-01", "2027-01-31")

    data = coordinator._build_student_data(STUDENT_ID, STUDENT_NAME, current, None, marks_list)

    # math average = 1.0, cz average = 5.0 -> mean = 3.0 (NOT vážený průměr
    # přes všechny 4 známky, který by dal (1+1+1+5)/4 = 2.0)
    assert data.average == 3.0


async def test_build_student_data_sorts_subjects_by_name(hass):
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(
        marks=[
            _mark("m1", subject_id="phy", value="1"),
            _mark("m2", subject_id="cz", value="1"),
        ],
        subject_names={"phy": "Fyzika", "cz": "Čeština"},
    )
    current = _semester("sem-1", "2026-09-01", "2027-01-31")

    data = coordinator._build_student_data(STUDENT_ID, STUDENT_NAME, current, None, marks_list)

    # Prosté řazení podle Unicode code pointu (ne locale-aware) — 'F' (U+0046)
    # je před 'Č' (U+010C), takže "Fyzika" předchází "Čeština".
    assert [s.subject_name for s in data.subjects] == ["Fyzika", "Čeština"]


async def test_build_student_data_limits_marks_per_subject(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator._marks_per_subject = 2
    marks_list = so_api.MarksList(
        marks=[
            _mark("m1", value="1", date="2026-09-01"),
            _mark("m2", value="2", date="2026-09-05"),
            _mark("m3", value="3", date="2026-09-10"),
        ],
        subject_names={"math": "Matematika"},
    )
    current = _semester("sem-1", "2026-09-01", "2027-01-31")

    data = coordinator._build_student_data(STUDENT_ID, STUDENT_NAME, current, None, marks_list)

    assert len(data.subjects[0].marks) == 2
    assert data.subjects[0].marks[0].id == "m3"  # nejnovější první
    assert data.subjects[0].count == 3  # count je z celého pololetí, ne jen zobrazených


async def test_build_student_data_adds_subjects_without_marks_from_timetable(hass):
    """Předmět z rozvrhu bez jediné známky se má objevit s prázdným polem marks."""
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(
        marks=[_mark("m1", subject_id="math", value="1")],
        subject_names={"math": "Matematika"},
    )
    current = _semester("sem-1", "2026-09-01", "2027-01-31")

    data = coordinator._build_student_data(
        STUDENT_ID,
        STUDENT_NAME,
        current,
        None,
        marks_list,
        timetable_subjects={"math": "Matematika", "tv": "Tělesná výchova"},
    )

    by_id = {s.subject_id: s for s in data.subjects}
    assert by_id["tv"].subject_name == "Tělesná výchova"
    assert by_id["tv"].average is None
    assert by_id["tv"].count == 0
    assert by_id["tv"].marks == []
    # Předmět, co už známku má, se z rozvrhu nepřepisuje na prázdný.
    assert by_id["math"].count == 1


async def test_build_student_data_school_year_and_previous_semester(hass):
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(marks=[], subject_names={})
    current = _semester("sem-1", "2026-09-01", "2027-01-31")
    previous = _semester("sem-0", "2026-02-01", "2026-06-30")

    data = coordinator._build_student_data(STUDENT_ID, STUDENT_NAME, current, previous, marks_list)

    assert data.school_year == "2026/2027"
    assert data.previous_semester_id == "sem-0"
    assert data.average is None


# ---------------------------------------------------------------------------
# _async_get_semesters_cached
# ---------------------------------------------------------------------------


async def test_semesters_cached_within_refresh_window_and_valid_period(hass):
    coordinator, _entry = _make_coordinator(hass)
    semesters = [_semester("s1", "2020-01-01", "2099-01-01")]
    coordinator.client.async_get_semesters = AsyncMock(return_value=semesters)

    first = await coordinator._async_get_semesters_cached(STUDENT_ID)
    second = await coordinator._async_get_semesters_cached(STUDENT_ID)

    assert first == second == semesters
    coordinator.client.async_get_semesters.assert_awaited_once()


async def test_semesters_cache_refetches_when_current_semester_ended(hass):
    coordinator, _entry = _make_coordinator(hass)
    expired = [_semester("s1", "2020-01-01", "2020-06-01")]
    coordinator.client.async_get_semesters = AsyncMock(return_value=expired)

    await coordinator._async_get_semesters_cached(STUDENT_ID)
    await coordinator._async_get_semesters_cached(STUDENT_ID)

    assert coordinator.client.async_get_semesters.await_count == 2


# ---------------------------------------------------------------------------
# _async_get_timetable_subjects
# ---------------------------------------------------------------------------


async def test_get_timetable_subjects_returns_client_result(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_timetable_subjects = AsyncMock(
        return_value={"tv": "Tělesná výchova"}
    )

    result = await coordinator._async_get_timetable_subjects(STUDENT_ID, "2026-09-01")

    assert result == {"tv": "Tělesná výchova"}
    coordinator.client.async_get_timetable_subjects.assert_awaited_once_with(
        STUDENT_ID, "2026-09-01T00:00:00", "2026-09-15T00:00:00"
    )


async def test_get_timetable_subjects_unparseable_date_returns_empty_without_calling_api(hass):
    coordinator, _entry = _make_coordinator(hass)

    result = await coordinator._async_get_timetable_subjects(STUDENT_ID, "")

    assert result == {}
    coordinator.client.async_get_timetable_subjects.assert_not_awaited()


async def test_get_timetable_subjects_api_error_is_swallowed(hass):
    """Nefatální — bez rozvrhu integrace pořád funguje jen s předměty ze známek."""
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_timetable_subjects = AsyncMock(
        side_effect=so_api.SkolaOnlineError("boom")
    )

    result = await coordinator._async_get_timetable_subjects(STUDENT_ID, "2026-09-01")

    assert result == {}


# ---------------------------------------------------------------------------
# _async_setup
# ---------------------------------------------------------------------------


async def test_async_setup_auth_error_raises_config_entry_auth_failed(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_login = AsyncMock(
        side_effect=so_api.SkolaOnlineAuthError("bad credentials")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_setup()


async def test_async_setup_connection_error_raises_update_failed(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_login = AsyncMock(side_effect=so_api.SkolaOnlineError("boom"))
    with pytest.raises(UpdateFailed):
        await coordinator._async_setup()


async def test_async_setup_loads_seen_marks_from_store(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_login = AsyncMock()
    coordinator._store.async_load = AsyncMock(return_value={STUDENT_ID: {"sem-1": ["m1"]}})

    await coordinator._async_setup()

    assert coordinator._seen_marks == {STUDENT_ID: {"sem-1": ["m1"]}}


# ---------------------------------------------------------------------------
# async_fetch_marks (pro service get_marks)
# ---------------------------------------------------------------------------


async def test_async_fetch_marks_includes_fields_not_in_attributes(hass):
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(
        marks=[
            so_api.Mark(
                id="m1",
                subject_id="math",
                value="1",
                weight=1.0,
                date="2026-09-10",
                theme="Zlomky",
                verbal_evaluation="Výborně",
                is_points=False,
            )
        ],
        subject_names={"math": "Matematika"},
    )
    coordinator.client.async_get_marks = AsyncMock(return_value=marks_list)

    result = await coordinator.async_fetch_marks(STUDENT_ID, "sem-1", None)

    assert result["marks"][0]["theme"] == "Zlomky"
    assert result["marks"][0]["verbal_evaluation"] == "Výborně"
    assert result["marks"][0]["subject_name"] == "Matematika"


async def test_async_fetch_marks_filters_by_subject(hass):
    coordinator, _entry = _make_coordinator(hass)
    marks_list = so_api.MarksList(
        marks=[_mark("m1", subject_id="math"), _mark("m2", subject_id="cz")],
        subject_names={},
    )
    coordinator.client.async_get_marks = AsyncMock(return_value=marks_list)

    result = await coordinator.async_fetch_marks(STUDENT_ID, "sem-1", "math")

    assert [m["id"] for m in result["marks"]] == ["m1"]


async def test_async_fetch_marks_auth_error_raises_config_entry_auth_failed(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_marks = AsyncMock(
        side_effect=so_api.SkolaOnlineAuthError("expired")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_fetch_marks(STUDENT_ID, "sem-1", None)


# ---------------------------------------------------------------------------
# _async_update_data
# ---------------------------------------------------------------------------


async def test_update_data_no_semesters_returns_empty_student_data(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_semesters = AsyncMock(return_value=[])

    result = await coordinator._async_update_data()

    assert result[STUDENT_ID].semester_id is None
    assert result[STUDENT_ID].average is None
    coordinator.client.async_get_marks.assert_not_called()


async def test_update_data_saves_store_when_marks_changed(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_semesters = AsyncMock(
        return_value=[_semester("sem-1", "2020-01-01", "2099-01-01")]
    )
    coordinator.client.async_get_marks = AsyncMock(
        return_value=so_api.MarksList(marks=[_mark("m1")], subject_names={"math": "Matematika"})
    )

    with patch.object(coordinator._store, "async_delay_save") as mock_save:
        result = await coordinator._async_update_data()

    assert result[STUDENT_ID].semester_id == "sem-1"
    mock_save.assert_called_once()


async def test_update_data_auth_error_raises_config_entry_auth_failed(hass):
    coordinator, _entry = _make_coordinator(hass)
    coordinator.client.async_get_semesters = AsyncMock(
        side_effect=so_api.SkolaOnlineAuthError("expired")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
