"""Binary sensor entities for ARTEMIS WebEvo."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArtemisRuntimeData
from .const import UNAVAILABLE_STATUS_CODES
from .coordinator import ArtemisOperationsCoordinator, ArtemisPlanningCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: ArtemisRuntimeData = entry.runtime_data
    async_add_entities(
        [
            ArtemisAvailableBinarySensor(runtime.planning),
            ArtemisOperationActiveBinarySensor(runtime.operations),
        ]
    )


class ArtemisAvailableBinarySensor(CoordinatorEntity[ArtemisPlanningCoordinator], BinarySensorEntity):
    _attr_name = "Disponible ARTEMIS"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisPlanningCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"artemis_{coordinator.staff_id}_available"

    @property
    def is_on(self) -> bool | None:
        current = self.coordinator.data.current
        if current is None:
            return None
        return current.code not in UNAVAILABLE_STATUS_CODES

    @property
    def icon(self) -> str:
        return "mdi:account-check" if self.is_on else "mdi:account-off"


class ArtemisOperationActiveBinarySensor(CoordinatorEntity[ArtemisOperationsCoordinator], BinarySensorEntity):
    _attr_name = "Active interventions ARTEMIS"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisOperationsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "artemis_operation_active"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.count > 0

    _attr_icon = "mdi:fire-alert"
