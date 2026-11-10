"""ARTEMIS WebEvo / Smartemis integration."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

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
    DOMAIN,
)
from .coordinator import (
    ArtemisCenterAvailabilityCoordinator,
    ArtemisOperationsCoordinator,
    ArtemisPlanningCoordinator,
)
from .smartemis import (
    SmartemisApi,
    SmartemisConnectionError,
    SmartemisServiceError,
    select_primary_center,
)
from .smartemis_coordinator import (
    SmartemisOperationsCoordinator,
    SmartemisPlanningCoordinator,
)

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


def _remove_legacy_entities(hass: HomeAssistant, staff_id: str) -> None:
    """Remove entity-registry entries retired by the lean 0.5.x model."""

    registry = er.async_get(hass)
    legacy = (
        ("sensor", f"artemis_{staff_id}_next_change"),
        ("sensor", "artemis_active_operations_count"),
        ("sensor", "artemis_available_personnel"),
        ("binary_sensor", f"artemis_{staff_id}_available"),
        ("binary_sensor", "artemis_operation_active"),
    )
    for domain, unique_id in legacy:
        if entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id):
            registry.async_remove(entity_id)


@dataclass(slots=True)
class ArtemisRuntimeData:
    backend: str
    api: ArtemisApi | SmartemisApi
    planning: ArtemisPlanningCoordinator | SmartemisPlanningCoordinator
    operations: ArtemisOperationsCoordinator | SmartemisOperationsCoordinator
    center: ArtemisCenterAvailabilityCoordinator | None = None


async def _async_setup_webevo(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> ArtemisRuntimeData:
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
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
    _remove_legacy_entities(hass, planning.staff_id)

    return ArtemisRuntimeData(
        backend=BACKEND_WEBEVO,
        api=api,
        planning=planning,
        operations=operations,
        center=center,
    )


async def _async_setup_smartemis(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> ArtemisRuntimeData:
    api = SmartemisApi(async_get_clientsession(hass))
    server = entry.data[CONF_SMARTEMIS_SERVER]
    phone_id = entry.data[CONF_SMARTEMIS_PHONE_ID]

    try:
        context = await api.async_get_user_context(server, phone_id=phone_id)
        centers = await api.async_get_centers(server, phone_id=phone_id)
        primary = select_primary_center(centers, context)
    except SmartemisServiceError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SmartemisConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    planning = SmartemisPlanningCoordinator(
        hass,
        entry,
        api,
        server,
        phone_id,
        primary,
    )
    operations = SmartemisOperationsCoordinator(
        hass,
        entry,
        api,
        server,
        phone_id,
        primary,
        context,
    )

    await planning.async_config_entry_first_refresh()
    await operations.async_config_entry_first_refresh()
    _remove_legacy_entities(hass, planning.staff_id)

    return ArtemisRuntimeData(
        backend=BACKEND_SMARTEMIS,
        api=api,
        planning=planning,
        operations=operations,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the selected ARTEMIS backend."""

    backend = entry.data.get(CONF_BACKEND, BACKEND_WEBEVO)
    if backend == BACKEND_WEBEVO:
        runtime = await _async_setup_webevo(hass, entry)
    elif backend == BACKEND_SMARTEMIS:
        runtime = await _async_setup_smartemis(hass, entry)
    else:
        raise ConfigEntryNotReady(f"Unsupported ARTEMIS backend: {backend}")

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: ArtemisRuntimeData = entry.runtime_data
    runtime.planning.async_shutdown_artemis()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Mark pre-0.6 entries explicitly as WebEvo without changing credentials."""

    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_BACKEND: BACKEND_WEBEVO},
            version=2,
        )
    return entry.version <= 2
