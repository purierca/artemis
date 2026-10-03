"""ARTEMIS WebEvo integration."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ArtemisApi, ArtemisAuthError, ArtemisConnectionError
from .const import CONF_CAS_SERVICE, CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DEFAULT_CAS_SERVICE
from .coordinator import (
    ArtemisCenterAvailabilityCoordinator,
    ArtemisOperationsCoordinator,
    ArtemisPlanningCoordinator,
)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


@dataclass(slots=True)
class ArtemisRuntimeData:
    api: ArtemisApi
    planning: ArtemisPlanningCoordinator
    operations: ArtemisOperationsCoordinator
    center: ArtemisCenterAvailabilityCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ARTEMIS WebEvo from a config entry."""
    session = async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(),
    )
    api = ArtemisApi(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        cas_service=entry.data.get(CONF_CAS_SERVICE, DEFAULT_CAS_SERVICE),
    )

    try:
        await api.async_login()
        init_data = await api.async_person_init()
        operations_init = await api.async_operations_init()
    except ArtemisAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ArtemisConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    if not init_data.get("staffId"):
        # A missing personal planning identity cannot be recovered by polling.
        raise ConfigEntryAuthFailed("ARTEMIS did not expose a personal planning identity")

    planning = ArtemisPlanningCoordinator(hass, entry, api, init_data)
    await planning.async_initialize_status_override()
    try:
        refresh_seconds = int(operations_init.get("refreshInterval") or 15)
    except (TypeError, ValueError):
        refresh_seconds = 15
    center = ArtemisCenterAvailabilityCoordinator(hass, entry, api, planning)
    operations = ArtemisOperationsCoordinator(
        hass,
        entry,
        api,
        refresh_seconds=refresh_seconds,
    )

    await planning.async_config_entry_first_refresh()
    await center.async_config_entry_first_refresh()
    await operations.async_config_entry_first_refresh()

    entry.runtime_data = ArtemisRuntimeData(
        api=api,
        planning=planning,
        operations=operations,
        center=center,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload ARTEMIS WebEvo."""
    runtime: ArtemisRuntimeData = entry.runtime_data
    runtime.planning.async_shutdown_artemis()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
