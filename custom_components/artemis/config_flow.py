"""Config flow for ARTEMIS WebEvo."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ArtemisApi, ArtemisAuthError, ArtemisConnectionError
from .const import (
    CONF_CAS_SERVICE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_CAS_SERVICE,
    DEFAULT_HOST,
    DOMAIN,
)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate credentials and return the personal planning identity."""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(),
        timeout=timeout,
    ) as session:
        api = ArtemisApi(
            session,
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            cas_service=data.get(CONF_CAS_SERVICE, DEFAULT_CAS_SERVICE),
        )
        await api.async_login()
        init_data = await api.async_person_init()

    staff_id = str(init_data.get("staffId") or "")
    if not staff_id:
        raise ArtemisAuthError("No personal planning identity")

    staff_name = staff_id
    for staff in init_data.get("staffs") or []:
        if str(staff.get("code")) == staff_id:
            staff_name = str(staff.get("name") or staff_id)
            break

    return {
        "staff_id": staff_id,
        "staff_name": staff_name,
        "unit_id": str(init_data.get("fireunitId") or api.unit_id or ""),
    }


class ArtemisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an ARTEMIS WebEvo config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].rstrip("/")
            try:
                info = await _validate(self.hass, user_input)
            except ArtemisAuthError:
                errors["base"] = "invalid_auth"
            except ArtemisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - config flows must surface an unknown error
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{info['staff_id']}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"ARTEMIS — {info['staff_name']}",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(),
                vol.Required(
                    CONF_CAS_SERVICE, default=DEFAULT_CAS_SERVICE
                ): TextSelector(),
                vol.Required(CONF_USERNAME): TextSelector(
                    TextSelectorConfig(autocomplete="username")
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            new_data = dict(entry.data)
            new_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            try:
                await _validate(self.hass, new_data)
            except ArtemisAuthError:
                errors["base"] = "invalid_auth"
            except ArtemisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    )
                }
            ),
            errors=errors,
        )
