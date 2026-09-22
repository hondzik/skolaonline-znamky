"""Sensor platforma Škola OnLine — jedna entita na dítě za aktuální pololetí.

`unique_id` je stabilní napříč pololetími/školními roky (neobsahuje
`semester_id`) — entita tedy nezaniká na přelomu pololetí, jen se jí změní
`state`/atributy na nové pololetí (viz `coordinator.py`).

Nově objevené dítě (přidané přes options flow) se přidá za běhu díky
coordinator listeneru, ne jen při startu platformy. Dítě, které zmizí
z konfigurace, se needstraňuje — jen zůstane `unavailable` (uživatel ho
může smazat ručně přes Nastavení -> Zařízení).
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SkolaOnlineCoordinator, StudentData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SkolaOnlineCoordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new_students() -> None:
        new_entities = [
            SkolaOnlineMarksSensor(coordinator, student_id)
            for student_id in coordinator.data
            if student_id not in added
        ]
        if new_entities:
            added.update(entity.student_id for entity in new_entities)
            async_add_entities(new_entities)

    _add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_students))


class SkolaOnlineMarksSensor(CoordinatorEntity[SkolaOnlineCoordinator], SensorEntity):
    """Vážený průměr aktuálního pololetí jednoho dítěte, rozpis po předmětech v atributech."""

    _attr_has_entity_name = True
    _attr_translation_key = "marks"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: SkolaOnlineCoordinator, student_id: str) -> None:
        super().__init__(coordinator)
        self.student_id = student_id
        parent_uid = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{parent_uid}_{student_id}_marks"
        data = self._data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{parent_uid}_{student_id}")},
            name=data.student_name if data else student_id,
            via_device_id=coordinator.hub_device_id,
        )

    @property
    def _data(self) -> StudentData | None:
        return self.coordinator.data.get(self.student_id)

    @property
    def available(self) -> bool:
        return super().available and self._data is not None

    @property
    def native_value(self) -> float | None:
        data = self._data
        return data.average if data else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._data
        if data is None:
            return {}
        return {
            "student_id": data.student_id,
            "student_name": data.student_name,
            "semester_id": data.semester_id,
            "semester_name": data.semester_name,
            "semester_from": data.semester_from,
            "semester_to": data.semester_to,
            "school_year": data.school_year,
            "previous_semester_id": data.previous_semester_id,
            "marks_count": data.marks_count,
            "subject_names": {s.subject_id: s.subject_name for s in data.subjects},
            "subjects": [
                {
                    "subject_id": s.subject_id,
                    "average": s.average,
                    "count": s.count,
                    "marks": [
                        {"id": m.id, "value": m.value, "weight": m.weight, "date": m.date}
                        for m in s.marks
                    ],
                }
                for s in data.subjects
            ],
        }
