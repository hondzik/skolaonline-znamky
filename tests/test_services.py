"""Testy pro service `skolaonline_znamky.get_marks` (`services.py`).

`coordinator.async_fetch_marks` je mockovaná — kryje se jen validace/routing
v `services.py` (dohledání config entry, kontrola stavu, kontrola dítěte),
samotné stahování známek má vlastní testy v `test_coordinator.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skolaonline_znamky.const import CONF_STUDENTS, DOMAIN
from custom_components.skolaonline_znamky.coordinator import SkolaOnlineCoordinator
from custom_components.skolaonline_znamky.services import (
    SERVICE_GET_MARKS,
    async_register_services,
)

USERNAME = "rodic@example.cz"
PASSWORD = "heslo123"
STUDENT_ID = "1"
OTHER_STUDENT_ID = "2"


def _entry_data() -> dict:
    return {"username": USERNAME, "password": PASSWORD, CONF_STUDENTS: {STUDENT_ID: "Anna"}}


def _make_loaded_entry(hass) -> tuple[MockConfigEntry, SkolaOnlineCoordinator]:
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data())
    entry.add_to_hass(hass)
    coordinator = SkolaOnlineCoordinator(hass, entry)
    coordinator.async_fetch_marks = AsyncMock(return_value={"marks": []})
    entry.runtime_data = coordinator
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry, coordinator


async def test_service_calls_coordinator_and_returns_response(hass):
    entry, coordinator = _make_loaded_entry(hass)
    async_register_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_MARKS,
        {
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            "student_id": STUDENT_ID,
            "semester_id": "sem-1",
            "subject_id": "math",
        },
        blocking=True,
        return_response=True,
    )

    coordinator.async_fetch_marks.assert_awaited_once_with(STUDENT_ID, "sem-1", "math")
    assert response == {"marks": []}


async def test_service_omits_optional_fields(hass):
    entry, coordinator = _make_loaded_entry(hass)
    async_register_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_MARKS,
        {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "student_id": STUDENT_ID},
        blocking=True,
        return_response=True,
    )

    coordinator.async_fetch_marks.assert_awaited_once_with(STUDENT_ID, None, None)


async def test_service_rejects_unknown_entry(hass):
    async_register_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_MARKS,
            {ATTR_CONFIG_ENTRY_ID: "does-not-exist", "student_id": STUDENT_ID},
            blocking=True,
            return_response=True,
        )


async def test_service_rejects_not_loaded_entry(hass):
    entry, coordinator = _make_loaded_entry(hass)
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    async_register_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_MARKS,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "student_id": STUDENT_ID},
            blocking=True,
            return_response=True,
        )
    coordinator.async_fetch_marks.assert_not_called()


async def test_service_rejects_unconfigured_student(hass):
    entry, coordinator = _make_loaded_entry(hass)
    async_register_services(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_MARKS,
            {ATTR_CONFIG_ENTRY_ID: entry.entry_id, "student_id": OTHER_STUDENT_ID},
            blocking=True,
            return_response=True,
        )
    coordinator.async_fetch_marks.assert_not_called()


def test_async_register_services_is_idempotent(hass):
    async_register_services(hass)
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_GET_MARKS)
