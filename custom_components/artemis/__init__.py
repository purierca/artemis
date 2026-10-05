"""ARTEMIS WebEvo integration."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ArtemisApi, ArtemisAuthError, ArtemisConnectionError
from .const import (
    CONF_CAS_SERVICE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_CAS_SERVICE,
    DOMAIN,
)
from .coordinator import (
    ArtemisCenterAvailabilityCoordinator,
    ArtemisOperationsCoordinator,
    ArtemisPlanningCoordinator,
)

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


def _remove_legacy_entities(hass: HomeAssistant, staff_id: str) -> None:
    """Remove entity-registry entries retired by the lean 0.5.x model.

    Home Assistant keeps entity registry entries even after an integration stops
    providing those entities. Without an explicit cleanup, upgrades from older
    ARTEMIS releases leave unavailable orphan entities in Settings > Entities.
    """
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

    # v0.5.x deliberately collapsed several entities into two lean sensors.
    # Clean the entity registry so upgrades do not leave the removed entities
    # visible as permanently unavailable.
    _remove_legacy_entities(hass, str(init_data["staffId"]))

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
