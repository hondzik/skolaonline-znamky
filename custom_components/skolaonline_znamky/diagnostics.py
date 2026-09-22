"""Diagnostics pro Škola OnLine — známky.

Nikdy nesmí unikat jméno dítěte, přihlašovací údaje nebo obsah známek —
diagnostika se běžně přikládá k GitHub issues. Přehled po dětech je proto
bez `student_id`/jména, jen jako pozicově řazený seznam agregátů.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_STUDENTS
from .coordinator import SkolaOnlineCoordinator

TO_REDACT = {"username", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: SkolaOnlineCoordinator = entry.runtime_data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "students_configured": len(
            entry.options.get(CONF_STUDENTS, entry.data.get(CONF_STUDENTS, {}))
        ),
        "students_summary": [
            {
                "semester_id": data.semester_id,
                "school_year": data.school_year,
                "marks_count": data.marks_count,
                "average": data.average,
                "subjects_count": len(data.subjects),
            }
            for data in coordinator.data.values()
        ],
    }
