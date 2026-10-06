"""Sensor entities for ARTEMIS WebEvo."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArtemisRuntimeData
from .coordinator import ArtemisOperationsCoordinator, ArtemisPlanningCoordinator
from .helpers import cycle_status_code


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the deliberately small ARTEMIS sensor surface."""
    runtime: ArtemisRuntimeData = entry.runtime_data
    async_add_entities(
        [
            ArtemisStatusSensor(runtime),
            ArtemisInterventionsSensor(runtime.operations),
        ]
    )


class ArtemisStatusSensor(CoordinatorEntity[ArtemisPlanningCoordinator], SensorEntity):
    """Expose personal status, next change and centre availability in one sensor."""

    _attr_name = "Statut ARTEMIS"
    _attr_icon = "mdi:account-clock"
    _attr_has_entity_name = False

    def __init__(self, runtime: ArtemisRuntimeData) -> None:
        super().__init__(runtime.planning)
        self.runtime = runtime
        # Keep the original unique id so existing installations retain their
        # sensor.statut_artemis entity id when upgrading.
        self._attr_unique_id = f"artemis_{runtime.planning.staff_id}_status"

    async def async_added_to_hass(self) -> None:
        """Also refresh the entity when the centre counter changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime.center.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        current = data.current if data else None
        return current.name if current else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        current = data.current if data else None
        next_status = self.coordinator.effective_next_status
        next_change = self.coordinator.effective_next_change
        cycle_until = self.coordinator.effective_override_boundary
        center = self.runtime.center.data if self.runtime.center.last_update_success else None

        current_code = current.code if current else None
        cycle_next = cycle_status_code(current_code) if current_code else None
        can_cycle = bool(
            cycle_next
            and not self.coordinator.read_only
            and self.coordinator.is_status_code_available(cycle_next)
        )

        return {
            "code": current_code,
            "since": data.current_since if data else None,
            "next_status": next_status.name if next_status else None,
            "next_code": next_status.code if next_status else None,
            "next_change": next_change,
            "available_personnel": center.available if center else None,
            "personnel_in_operation": center.in_operation if center else None,
            "center": data.unit_id if data else None,
            "person": data.staff_name if data else None,
            "override_active": self.coordinator.status_override_active,
            "writable": not self.coordinator.read_only,
            "can_cycle": can_cycle,
            "cycle_next_code": cycle_next if can_cycle else None,
            "cycle_until": cycle_until,
        }


class ArtemisInterventionsSensor(
    CoordinatorEntity[ArtemisOperationsCoordinator], SensorEntity
):
    """Expose the current ARTEMIS operations snapshot as structured attributes."""

    _attr_name = "Interventions ARTEMIS"
    _attr_icon = "mdi:fire-alert"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisOperationsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "artemis_interventions"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.count

    @property
    def extra_state_attributes(self) -> dict:
        return {"interventions": list(self.coordinator.data.operations)}
