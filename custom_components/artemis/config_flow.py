"""Config flow for ARTEMIS WebEvo and Smartemis."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ArtemisApi, ArtemisAuthError, ArtemisConnectionError
from .const import (
    BACKEND_SMARTEMIS,
    BACKEND_WEBEVO,
    CONF_BACKEND,
    CONF_CAS_SERVICE,
    CONF_HOST,
    CONF_MOBILE,
    CONF_PASSWORD,
    CONF_PERSON_ID,
    CONF_SMARTEMIS_ORGANIZATION,
    CONF_SMARTEMIS_PHONE_ID,
    CONF_SMARTEMIS_SERVER,
    CONF_USERNAME,
    DEFAULT_CAS_SERVICE,
    DEFAULT_HOST,
    DOMAIN,
)
from .smartemis import (
    SmartemisApi,
    SmartemisConnectionError,
    SmartemisSdis,
    SmartemisServiceError,
    select_primary_center,
)


async def _validate_webevo(data: dict[str, Any]) -> dict[str, str]:
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

    staff_name = next(
        (
            str(staff.get("name") or staff_id)
            for staff in (init_data.get("staffs") or [])
            if str(staff.get("code")) == staff_id
        ),
        staff_id,
    )
    return {"staff_id": staff_id, "staff_name": staff_name}


class ArtemisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one ARTEMIS backend."""

    VERSION = 2

    def _phone_id(self) -> str:
        phone_id = getattr(self, "_generated_phone_id", None)
        if phone_id is None:
            phone_id = str(uuid4())
            self._generated_phone_id = phone_id
        return phone_id

    def _smartemis_api(self) -> SmartemisApi:
        return SmartemisApi(async_get_clientsession(self.hass))

    async def _discover_smartemis(self) -> dict[str, SmartemisSdis]:
        servers = await self._smartemis_api().async_discover_sdis(self._phone_id())
        return {server.urls[0]: server for server in servers if server.urls}

    async def _finish_smartemis(
        self,
        *,
        server: str,
        organization: str,
        phone_id: str,
    ) -> ConfigFlowResult:
        api = self._smartemis_api()
        context = await api.async_get_user_context(server, phone_id=phone_id)
        centers = await api.async_get_centers(server, phone_id=phone_id)
        primary = select_primary_center(centers, context)

        await self.async_set_unique_id(
            f"smartemis:{organization}:{primary.person_id or primary.matricule}"
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Smartemis — {organization} — {primary.name}",
            data={
                CONF_BACKEND: BACKEND_SMARTEMIS,
                CONF_SMARTEMIS_SERVER: server,
                CONF_SMARTEMIS_ORGANIZATION: organization,
                CONF_SMARTEMIS_PHONE_ID: phone_id,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=[BACKEND_WEBEVO, BACKEND_SMARTEMIS],
        )

    async def async_step_webevo(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].rstrip("/")
            try:
                info = await _validate_webevo(user_input)
            except ArtemisAuthError:
                errors["base"] = "invalid_auth"
            except ArtemisConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{info['staff_id']}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"ARTEMIS WebEvo — {info['staff_name']}",
                    data={CONF_BACKEND: BACKEND_WEBEVO, **user_input},
                )

        return self.async_show_form(
            step_id="webevo",
            data_schema=vol.Schema(
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
            ),
            errors=errors,
        )

    async def async_step_smartemis(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        try:
            servers = getattr(self, "_smartemis_servers", None)
            if servers is None:
                servers = await self._discover_smartemis()
                self._smartemis_servers = servers
        except SmartemisConnectionError:
            return self.async_show_form(
                step_id="smartemis",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )
        except SmartemisServiceError:
            return self.async_show_form(
                step_id="smartemis",
                data_schema=vol.Schema({}),
                errors={"base": "invalid_auth"},
            )

        if user_input is not None:
            server_url = user_input[CONF_SMARTEMIS_SERVER]
            selected = servers.get(server_url)
            if selected is None:
                errors["base"] = "unknown"
            else:
                try:
                    auth_needed = await self._smartemis_api().async_record_subscriber(
                        server_url,
                        phone_id=self._phone_id(),
                        person_id=user_input[CONF_PERSON_ID],
                        mobile=user_input[CONF_MOBILE],
                        organization=selected.name,
                    )
                except SmartemisConnectionError:
                    errors["base"] = "cannot_connect"
                except SmartemisServiceError:
                    errors["base"] = "invalid_auth"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                else:
                    self._smartemis_pending = {
                        "server": server_url,
                        "organization": selected.name,
                        "phone_id": self._phone_id(),
                    }
                    if auth_needed:
                        return await self.async_step_smartemis_auth()
                    try:
                        return await self._finish_smartemis(**self._smartemis_pending)
                    except SmartemisConnectionError:
                        errors["base"] = "cannot_connect"
                    except SmartemisServiceError:
                        errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="smartemis",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SMARTEMIS_SERVER): vol.In(
                        {url: server.name for url, server in servers.items()}
                    ),
                    vol.Required(CONF_PERSON_ID): TextSelector(
                        TextSelectorConfig(autocomplete="username")
                    ),
                    vol.Required(CONF_MOBILE): TextSelector(
                        TextSelectorConfig(autocomplete="tel")
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_smartemis_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        pending = getattr(self, "_smartemis_pending", None)
        if pending is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            try:
                await self._smartemis_api().async_authentify(
                    pending["server"],
                    phone_id=pending["phone_id"],
                    password=user_input[CONF_PASSWORD],
                )
                return await self._finish_smartemis(**pending)
            except SmartemisConnectionError:
                errors["base"] = "cannot_connect"
            except SmartemisServiceError:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="smartemis_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="one-time-code",
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="unknown")
        if self._reauth_entry.data.get(CONF_BACKEND, BACKEND_WEBEVO) == BACKEND_SMARTEMIS:
            return await self.async_step_reauth_smartemis()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry: ConfigEntry | None = getattr(self, "_reauth_entry", None)
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            new_data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate_webevo(new_data)
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
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def _finish_smartemis_reauth(self, entry: ConfigEntry) -> ConfigFlowResult:
        api = self._smartemis_api()
        server = entry.data[CONF_SMARTEMIS_SERVER]
        phone_id = entry.data[CONF_SMARTEMIS_PHONE_ID]
        context = await api.async_get_user_context(server, phone_id=phone_id)
        centers = await api.async_get_centers(server, phone_id=phone_id)
        select_primary_center(centers, context)
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")

    async def async_step_reauth_smartemis(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry: ConfigEntry | None = getattr(self, "_reauth_entry", None)
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            try:
                auth_needed = await self._smartemis_api().async_record_subscriber(
                    entry.data[CONF_SMARTEMIS_SERVER],
                    phone_id=entry.data[CONF_SMARTEMIS_PHONE_ID],
                    person_id=user_input[CONF_PERSON_ID],
                    mobile=user_input[CONF_MOBILE],
                    organization=entry.data[CONF_SMARTEMIS_ORGANIZATION],
                )
                if auth_needed:
                    return await self.async_step_reauth_smartemis_auth()
                return await self._finish_smartemis_reauth(entry)
            except SmartemisConnectionError:
                errors["base"] = "cannot_connect"
            except SmartemisServiceError:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reauth_smartemis",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PERSON_ID): TextSelector(
                        TextSelectorConfig(autocomplete="username")
                    ),
                    vol.Required(CONF_MOBILE): TextSelector(
                        TextSelectorConfig(autocomplete="tel")
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth_smartemis_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry: ConfigEntry | None = getattr(self, "_reauth_entry", None)
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            try:
                await self._smartemis_api().async_authentify(
                    entry.data[CONF_SMARTEMIS_SERVER],
                    phone_id=entry.data[CONF_SMARTEMIS_PHONE_ID],
                    password=user_input[CONF_PASSWORD],
                )
                return await self._finish_smartemis_reauth(entry)
            except SmartemisConnectionError:
                errors["base"] = "cannot_connect"
            except SmartemisServiceError:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reauth_smartemis_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="one-time-code",
                        )
                    )
                }
            ),
            errors=errors,
        )
