"""Config flow pro Škola OnLine — známky.

Krok 1 (`user`): přihlašovací údaje, ověřené živým loginem přes `so_api`.
Zároveň se z účtu zkusí zjistit děti (`so_api.async_list_students`) — pole
`children` u `/v1/user` NENÍ v oficiální OpenAPI spec (viz CLAUDE.md), proto
krok 2 vždy nabízí i ruční zadání `studentId`, kdyby se automatické zjištění
nepovedlo nebo bylo neúplné.

Krok 2 (`children`): multi-select nalezených dětí + textové pole pro ruční
přidání dalších (`id:Jméno`, čárkou oddělené).

Options flow (menu): "children" pro dodatečnou změnu sledovaných dětí
(přihlašovací údaje se znovu použijí z `entry.data`) a "settings" pro
interval stahování a počet známek držených v atributech na předmět.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client, selector

from . import so_api
from .const import (
    CONF_MARKS_PER_SUBJECT,
    CONF_STUDENTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    DEFAULT_UPDATE_MINUTES,
    DOMAIN,
    MARKS_PER_SUBJECT,
)

CONF_STUDENTS_SELECTED = "students_selected"
CONF_MANUAL_STUDENTS = "manual_students"


async def _async_login_and_list_students(
    hass: HomeAssistant, username: str, password: str
) -> tuple[str, list[so_api.Student]]:
    """Přihlásí se a vrátí (parent_uid, nalezené děti). Volá se přímo (aiohttp je async)."""
    client = so_api.SkolaOnlineClient(aiohttp_client.async_get_clientsession(hass))
    await client.async_login(username, password)
    user = await client.async_get_user()
    parent_uid = str(user.get("userUID") or user.get("personID") or username)
    students = await client.async_list_students()
    return parent_uid, students


async def _async_login_only(hass: HomeAssistant, username: str, password: str) -> None:
    client = so_api.SkolaOnlineClient(aiohttp_client.async_get_clientsession(hass))
    await client.async_login(username, password)


def _children_schema(
    discovered: list[so_api.Student], current: dict[str, str] | None = None
) -> vol.Schema:
    schema_dict: dict[Any, Any] = {}
    if discovered:
        options = [{"value": s.id, "label": f"{s.name} ({s.id})"} for s in discovered]
        defaults = (
            [s.id for s in discovered if s.id in current]
            if current is not None
            else [s.id for s in discovered]
        )
        schema_dict[vol.Optional(CONF_STUDENTS_SELECTED, default=defaults)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        )
    schema_dict[vol.Optional(CONF_MANUAL_STUDENTS, default="")] = selector.TextSelector()
    return vol.Schema(schema_dict)


def _parse_children_input(
    user_input: dict[str, Any], discovered: list[so_api.Student]
) -> dict[str, str]:
    by_id = {s.id: s.name for s in discovered}
    result: dict[str, str] = {}
    for student_id in user_input.get(CONF_STUDENTS_SELECTED, []):
        result[student_id] = by_id.get(student_id, student_id)
    for part in (user_input.get(CONF_MANUAL_STUDENTS) or "").split(","):
        part = part.strip()
        if not part:
            continue
        student_id, _, name = part.partition(":")
        student_id = student_id.strip()
        result[student_id] = name.strip() or student_id
    return result


def _merged_options(entry: config_entries.ConfigEntry, **updates: Any) -> dict[str, Any]:
    merged = dict(entry.options)
    merged.update(updates)
    return merged


class SkolaOnlineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nastavení integrace: přihlášení -> výběr sledovaných dětí."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._discovered_students: list[so_api.Student] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parent_uid, students = await _async_login_and_list_students(
                    self.hass, user_input["username"], user_input["password"]
                )
            except so_api.SkolaOnlineAuthError:
                errors["base"] = "invalid_auth"
            except so_api.SkolaOnlineError:
                errors["base"] = "cannot_connect"
            else:
                self._username = user_input["username"]
                self._password = user_input["password"]
                self._discovered_students = students
                await self.async_set_unique_id(parent_uid)
                self._abort_if_unique_id_configured()
                return await self.async_step_children()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_children(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            students = _parse_children_input(user_input, self._discovered_students)
            if not students:
                errors["base"] = "no_students_selected"
            else:
                return self.async_create_entry(
                    title=f"Škola OnLine ({self._username})",
                    data={
                        "username": self._username,
                        "password": self._password,
                        CONF_STUDENTS: students,
                    },
                )

        return self.async_show_form(
            step_id="children",
            data_schema=_children_schema(self._discovered_students),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Spustí se, když `coordinator.py` vyhodí `ConfigEntryAuthFailed`."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _async_login_only(
                    self.hass, reauth_entry.data["username"], user_input["password"]
                )
            except so_api.SkolaOnlineAuthError:
                errors["base"] = "invalid_auth"
            except so_api.SkolaOnlineError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, "password": user_input["password"]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"username": reauth_entry.data["username"]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "SkolaOnlineOptionsFlow":
        return SkolaOnlineOptionsFlow()


class SkolaOnlineOptionsFlow(config_entries.OptionsFlow):
    """Menu: změna sledovaných dětí, nebo intervalu/počtu známek v atributech.

    Pozn.: `self.config_entry` se NEnastavuje v `__init__` — novější HA
    dodává config_entry base třídou/flow manažerem automaticky; explicitní
    nastavení by options flow shodilo na 500 Internal Server Error.
    """

    def __init__(self) -> None:
        self._discovered_students: list[so_api.Student] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["children", "settings"])

    async def async_step_children(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._discovered_students is None:
            try:
                _, self._discovered_students = await _async_login_and_list_students(
                    self.hass,
                    self.config_entry.data["username"],
                    self.config_entry.data["password"],
                )
            except so_api.SkolaOnlineAuthError:
                return self.async_abort(reason="invalid_auth")
            except so_api.SkolaOnlineError:
                return self.async_abort(reason="cannot_connect")

        errors: dict[str, str] = {}
        current = self.config_entry.options.get(
            CONF_STUDENTS, self.config_entry.data.get(CONF_STUDENTS, {})
        )
        if user_input is not None:
            students = _parse_children_input(user_input, self._discovered_students)
            if not students:
                errors["base"] = "no_students_selected"
            else:
                return self.async_create_entry(
                    data=_merged_options(self.config_entry, **{CONF_STUDENTS: students})
                )

        schema = _children_schema(self._discovered_students, current)
        return self.async_show_form(step_id="children", data_schema=schema, errors=errors)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data=_merged_options(
                    self.config_entry,
                    **{
                        CONF_UPDATE_INTERVAL_MINUTES: int(
                            user_input[CONF_UPDATE_INTERVAL_MINUTES]
                        ),
                        CONF_MARKS_PER_SUBJECT: int(user_input[CONF_MARKS_PER_SUBJECT]),
                    },
                )
            )

        current_interval = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_MINUTES
        )
        current_marks = self.config_entry.options.get(
            CONF_MARKS_PER_SUBJECT, MARKS_PER_SUBJECT
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL_MINUTES, default=current_interval
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=360, step=5, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_MARKS_PER_SUBJECT, default=current_marks
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=20, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
