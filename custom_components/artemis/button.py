"""Button entities for ARTEMIS WebEvo."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArtemisRuntimeData
from .coordinator import ArtemisPlanningCoordinator
from .helpers import cycle_status_code


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ARTEMIS button entities."""
    runtime: ArtemisRuntimeData = entry.runtime_data
    async_add_entities([ArtemisCycleStatusButton(runtime)])


class ArtemisCycleStatusButton(
    CoordinatorEntity[ArtemisPlanningCoordinator], ButtonEntity
):
    """Cycle the current personal status IND -> DI1 -> AS1 -> IND."""

    _attr_name = "Cycle ARTEMIS status"
    _attr_icon = "mdi:account-switch"
    _attr_has_entity_name = False

    def __init__(self, runtime: ArtemisRuntimeData) -> None:
        super().__init__(runtime.planning)
        self.runtime = runtime
        self._attr_unique_id = f"artemis_{runtime.planning.staff_id}_cycle_status"

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        if (
            not self.coordinator.last_update_success
            or data is None
            or data.current is None
            or self.coordinator.read_only
        ):
            return False
        next_code = cycle_status_code(data.current.code)
        return (
            next_code is not None
            and self.coordinator.is_status_code_available(next_code)
        )

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        current_code = data.current.code if data and data.current else None
        return {
            "current_code": current_code,
            "next_code": cycle_status_code(current_code) if current_code else None,
            "override_until": self.coordinator.effective_override_boundary,
        }

    async def async_press(self) -> None:
        """Cycle current availability using the safest known planning boundary."""
        await self.coordinator.async_cycle_status()
        # Reflect the new native ARTEMIS centre counter immediately as well.
        await self.runtime.center.async_request_refresh()
