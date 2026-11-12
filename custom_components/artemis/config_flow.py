"""Config flow for ARTEMIS WebEvo and Smartemis."""

from __future__ import annotations

import logging
from typing import Any

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
    CONF_PASSWORD,
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
)
from .smartemis_capture import SmartemisCaptureProxy

_LOGGER = logging.getLogger(__name__)


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

    VERSION = 3

    def _smartemis_api(self) -> SmartemisApi:
        return SmartemisApi(async_get_clientsession(self.hass))

    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
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

    async def _async_prepare_smartemis(
        self,
        phone_id: str,
    ) -> tuple[ConfigFlowResult | None, str | None]:
        """Discover Smartemis services from an existing device identity."""

        try:
            services = await self._smartemis_api().async_discover_sdis(phone_id)
        except SmartemisConnectionError:
            return None, "cannot_connect"
        except SmartemisServiceError:
            return None, "invalid_auth"
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Unexpected Smartemis discovery error; phoneId is not logged"
            )
            return None, "unknown"

        if not services:
            return None, "cannot_connect"

        self._smartemis_phone_id = phone_id
        self._smartemis_services = {
            service.urls[0]: service for service in services if service.urls
        }
        _LOGGER.debug(
            "Smartemis identity accepted for discovery services=%s phone_id_logged=false",
            len(self._smartemis_services),
        )
        return await self.async_step_smartemis_service(), None

    async def _async_stop_capture(self, reason: str) -> None:
        proxy: SmartemisCaptureProxy | None = getattr(
            self, "_smartemis_capture_proxy", None
        )
        if proxy is not None:
            await proxy.async_stop(reason=reason)
            self._smartemis_capture_proxy = None

    async def async_step_smartemis(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose automatic local capture or manual phoneId entry."""

        return self.async_show_menu(
            step_id="smartemis",
            menu_options=["smartemis_auto", "smartemis_manual"],
        )

    async def async_step_smartemis_auto(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Capture the existing Smartemis phoneId through a temporary local proxy."""

        errors: dict[str, str] = {}
        proxy: SmartemisCaptureProxy | None = getattr(
            self, "_smartemis_capture_proxy", None
        )
        if proxy is None:
            proxy = SmartemisCaptureProxy(async_get_clientsession(self.hass))
            try:
                await proxy.async_start()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Unable to start local Smartemis capture proxy; "
                    "no phoneId was captured"
                )
                return self.async_show_form(
                    step_id="smartemis_auto",
                    data_schema=vol.Schema({}),
                    errors={"base": "capture_unavailable"},
                    description_placeholders={
                        "pac_url": "unavailable",
                        "proxy_host": "unavailable",
                        "proxy_port": "unavailable",
                    },
                )
            self._smartemis_capture_proxy = proxy

        if user_input is not None:
            phone_id = proxy.captured_phone_id
            if phone_id:
                await self._async_stop_capture("captured")
                result, error = await self._async_prepare_smartemis(phone_id)
                if result is not None:
                    return result
                errors["base"] = error or "unknown"
            else:
                errors["base"] = "phone_id_not_detected"

        return self.async_show_form(
            step_id="smartemis_auto",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "pac_url": proxy.pac_url,
                "proxy_host": proxy.host,
                "proxy_port": str(proxy.port),
            },
        )

    async def async_step_smartemis_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reuse a phoneId copied from the official Smartemis app."""

        errors: dict[str, str] = {}
        if user_input is not None:
            phone_id = str(user_input[CONF_SMARTEMIS_PHONE_ID]).strip()
            result, error = await self._async_prepare_smartemis(phone_id)
            if result is not None:
                return result
            errors["base"] = error or "unknown"

        return self.async_show_form(
            step_id="smartemis_manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SMARTEMIS_PHONE_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_smartemis_service(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        phone_id = getattr(self, "_smartemis_phone_id", None)
        services: dict[str, SmartemisSdis] | None = getattr(
            self, "_smartemis_services", None
        )
        if not phone_id or not services:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            server = user_input[CONF_SMARTEMIS_SERVER]
            if server not in services:
                errors["base"] = "unknown"
            else:
                try:
                    subscriber, _, primary = (
                        await self._smartemis_api().async_get_identity(
                            server, phone_id=phone_id
                        )
                    )
                except SmartemisConnectionError:
                    errors["base"] = "cannot_connect"
                except SmartemisServiceError:
                    errors["base"] = "invalid_auth"
                except Exception:  # noqa: BLE001
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(
                        f"smartemis:{subscriber.organization}:{subscriber.person_id}"
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=(
                            f"Smartemis — {subscriber.organization} — {primary.name}"
                        ),
                        data={
                            CONF_BACKEND: BACKEND_SMARTEMIS,
                            CONF_SMARTEMIS_SERVER: server,
                            CONF_SMARTEMIS_PHONE_ID: phone_id,
                        },
                    )

        return self.async_show_form(
            step_id="smartemis_service",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SMARTEMIS_SERVER): vol.In(
                        {url: service.name for url, service in services.items()}
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, _entry_data: dict[str, Any]
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

    async def async_step_reauth_smartemis(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace only the reused mobile-app identity; never register a new one."""

        errors: dict[str, str] = {}
        entry: ConfigEntry | None = getattr(self, "_reauth_entry", None)
        if entry is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            phone_id = str(user_input[CONF_SMARTEMIS_PHONE_ID]).strip()
            try:
                await self._smartemis_api().async_get_identity(
                    entry.data[CONF_SMARTEMIS_SERVER],
                    phone_id=phone_id,
                )
            except SmartemisConnectionError:
                errors["base"] = "cannot_connect"
            except SmartemisServiceError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_SMARTEMIS_PHONE_ID: phone_id},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_smartemis",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SMARTEMIS_PHONE_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )
