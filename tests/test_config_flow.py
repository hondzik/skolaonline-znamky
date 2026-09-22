"""Testy pro config_flow.py (ConfigFlow + OptionsFlow) přes reálný HA loader.

`SkolaOnlineClient` metody se patchují přímo na třídě (`so_api.SkolaOnline
Client.async_login` apod.) — instance se pořád vytváří s reálnou aiohttp
session z `hass`, jen se nepůjde po síti.

`test_options_children_step_shows_menu` (resp. init) je regresní test na
stejný vzor jako u `predistribuce`: `SkolaOnlineOptionsFlow.__init__` NESMÍ
sám nastavovat `self.config_entry` (viz komentář v `config_flow.py`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skolaonline_znamky import so_api
from custom_components.skolaonline_znamky.const import (
    CONF_MARKS_PER_SUBJECT,
    CONF_STUDENTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

USERNAME = "rodic@example.cz"
PASSWORD = "heslo123"
PARENT_UID = "parent-uid-1"
STUDENT = so_api.Student(id="1", name="Anna")


def _entry_data() -> dict:
    return {"username": USERNAME, "password": PASSWORD, CONF_STUDENTS: {STUDENT.id: STUDENT.name}}


def _patch_login_and_list(students: list[so_api.Student] | None = None, user_id: str = PARENT_UID):
    """Patchne login + zjištění dětí; `async_get_user` vrací `userUID` pro unique_id."""
    return (
        patch.object(so_api.SkolaOnlineClient, "async_login", AsyncMock()),
        patch.object(
            so_api.SkolaOnlineClient, "async_get_user", AsyncMock(return_value={"userUID": user_id})
        ),
        patch.object(
            so_api.SkolaOnlineClient,
            "async_list_students",
            AsyncMock(return_value=students if students is not None else [STUDENT]),
        ),
    )


# ---------------------------------------------------------------------------
# async_step_user
# ---------------------------------------------------------------------------


async def test_user_step_shows_form_initially(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_success_advances_to_children(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login, get_user, list_students = _patch_login_and_list()
    with login, get_user, list_students:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "children"


async def test_user_step_invalid_auth_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch.object(
        so_api.SkolaOnlineClient,
        "async_login",
        AsyncMock(side_effect=so_api.SkolaOnlineAuthError("nope")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": "spatne"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_cannot_connect_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch.object(
        so_api.SkolaOnlineClient,
        "async_login",
        AsyncMock(side_effect=so_api.SkolaOnlineError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
# async_step_children / plný flow
# ---------------------------------------------------------------------------


async def test_full_flow_creates_entry_with_discovered_student(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login, get_user, list_students = _patch_login_and_list()
    with login, get_user, list_students:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"students_selected": [STUDENT.id], "manual_students": ""}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Škola OnLine ({USERNAME})"
    assert result["data"] == {
        "username": USERNAME,
        "password": PASSWORD,
        CONF_STUDENTS: {STUDENT.id: STUDENT.name},
    }


async def test_children_step_manual_entry_fallback_when_nothing_discovered(hass):
    """Fallback pro nepotvrzené pole `children` u /v1/user — viz CLAUDE.md."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login, get_user, list_students = _patch_login_and_list(students=[])
    with login, get_user, list_students:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"manual_students": "99:Ruční Dítě"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STUDENTS] == {"99": "Ruční Dítě"}


async def test_children_step_requires_at_least_one_student(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login, get_user, list_students = _patch_login_and_list(students=[])
    with login, get_user, list_students:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"manual_students": ""}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_students_selected"}


async def test_duplicate_account_aborts_already_configured(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data()).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    login, get_user, list_students = _patch_login_and_list()
    with login, get_user, list_students:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": PASSWORD}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# reauth
# ---------------------------------------------------------------------------


async def test_reauth_success_updates_password(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    assert result["step_id"] == "reauth_confirm"

    with patch.object(so_api.SkolaOnlineClient, "async_login", AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "nove-heslo"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "nove-heslo"


async def test_reauth_invalid_password_shows_error(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
    )
    with patch.object(
        so_api.SkolaOnlineClient,
        "async_login",
        AsyncMock(side_effect=so_api.SkolaOnlineAuthError("nope")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "spatne"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["password"] == PASSWORD  # nezměněno


# ---------------------------------------------------------------------------
# options flow
# ---------------------------------------------------------------------------


async def test_options_init_shows_menu(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(result["menu_options"]) == {"children", "settings"}


async def test_options_children_updates_configured_students(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    login, get_user, list_students = _patch_login_and_list()
    with login, get_user, list_students:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "children"}
        )
        assert result["step_id"] == "children"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"students_selected": [STUDENT.id], "manual_students": "99:Nové"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STUDENTS] == {STUDENT.id: STUDENT.name, "99": "Nové"}


async def test_options_children_invalid_auth_aborts(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(
        so_api.SkolaOnlineClient,
        "async_login",
        AsyncMock(side_effect=so_api.SkolaOnlineAuthError("nope")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "children"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_auth"


async def test_options_settings_updates_entry_options(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARENT_UID, data=_entry_data())
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL_MINUTES: 60, CONF_MARKS_PER_SUBJECT: 3}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_UPDATE_INTERVAL_MINUTES] == 60
    assert entry.options[CONF_MARKS_PER_SUBJECT] == 3
