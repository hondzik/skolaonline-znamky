"""Service `skolaonline_znamky.get_marks` — kompletní známky na vyžádání.

Primární API pro tlačítko "více" v budoucí Lovelace kartě: sensor entita
(`sensor.py`) drží v atributech jen posledních pár známek na předmět, tahle
služba stáhne čerstvě z API úplný seznam (včetně `theme`/`verbal_evaluation`,
které v atributech záměrně nejsou) pro konkrétní dítě/pololetí/předmět.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CONF_STUDENTS, DOMAIN

SERVICE_GET_MARKS = "get_marks"
ATTR_STUDENT_ID = "student_id"
ATTR_SEMESTER_ID = "semester_id"
ATTR_SUBJECT_ID = "subject_id"

SERVICE_GET_MARKS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_STUDENT_ID): cv.string,
        vol.Optional(ATTR_SEMESTER_ID): cv.string,
        vol.Optional(ATTR_SUBJECT_ID): cv.string,
    }
)


def _get_loaded_entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return entry


def async_register_services(hass: HomeAssistant) -> None:
    """Zaregistruje service, pokud ještě není (voláno z každého `async_setup_entry`)."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_MARKS):
        return

    async def _async_handle_get_marks(call: ServiceCall) -> ServiceResponse:
        entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        student_id = call.data[ATTR_STUDENT_ID]
        configured_students = entry.options.get(
            CONF_STUDENTS, entry.data.get(CONF_STUDENTS, {})
        )
        if student_id not in configured_students:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="student_not_configured",
                translation_placeholders={"student_id": student_id},
            )

        return await entry.runtime_data.async_fetch_marks(
            student_id, call.data.get(ATTR_SEMESTER_ID), call.data.get(ATTR_SUBJECT_ID)
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MARKS,
        _async_handle_get_marks,
        schema=SERVICE_GET_MARKS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
