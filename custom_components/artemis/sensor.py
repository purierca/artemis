"""Sensor entities for ARTEMIS WebEvo."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ArtemisRuntimeData
from .coordinator import (
    ArtemisCenterAvailabilityCoordinator,
    ArtemisOperationsCoordinator,
    ArtemisPlanningCoordinator,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: ArtemisRuntimeData = entry.runtime_data
    async_add_entities(
        [
            ArtemisStatusSensor(runtime.planning),
            ArtemisNextChangeSensor(runtime.planning),
            ArtemisActiveOperationsSensor(runtime.operations),
            ArtemisAvailablePersonnelSensor(runtime.center),
        ]
    )


class ArtemisStatusSensor(CoordinatorEntity[ArtemisPlanningCoordinator], SensorEntity):
    _attr_name = "Statut ARTEMIS"
    _attr_icon = "mdi:account-clock"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisPlanningCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"artemis_{coordinator.staff_id}_status"

    @property
    def native_value(self) -> str | None:
        current = self.coordinator.data.current
        return current.name if current else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        next_status = self.coordinator.effective_next_status
        next_change = self.coordinator.effective_next_change
        return {
            "code": data.current.code if data.current else None,
            "depuis": data.current_since,
            "prochain_statut": next_status.name if next_status else None,
            "prochain_code": next_status.code if next_status else None,
            "prochain_changement": next_change,
            "override_actif": self.coordinator.status_override_active,
            "personne": data.staff_name,
            "centre": data.unit_id,
            "horizon_semaines": data.lookahead_weeks,
        }


class ArtemisNextChangeSensor(CoordinatorEntity[ArtemisPlanningCoordinator], SensorEntity):
    _attr_name = "Prochain changement ARTEMIS"
    _attr_icon = "mdi:list-status"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisPlanningCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"artemis_{coordinator.staff_id}_next_change"

    @property
    def native_value(self) -> str | None:
        next_status = self.coordinator.effective_next_status
        next_change = self.coordinator.effective_next_change
        if next_status is None or next_change is None:
            return None
        return f"{next_status.code} · {next_change.strftime('%d/%m %H:%M')}"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        next_status = self.coordinator.effective_next_status
        next_change = self.coordinator.effective_next_change
        return {
            "statut_actuel": data.current.name if data.current else None,
            "prochain_statut": next_status.name if next_status else None,
            "prochain_code": next_status.code if next_status else None,
            "date": next_change,
            "override_actif": self.coordinator.status_override_active,
            "horizon_semaines": data.lookahead_weeks,
        }


class ArtemisActiveOperationsSensor(CoordinatorEntity[ArtemisOperationsCoordinator], SensorEntity):
    _attr_name = "Active interventions count ARTEMIS"
    _attr_icon = "mdi:fire-alert"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisOperationsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "artemis_active_operations_count"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.count


class ArtemisAvailablePersonnelSensor(
    CoordinatorEntity[ArtemisCenterAvailabilityCoordinator], SensorEntity
):
    _attr_name = "Available personnel ARTEMIS"
    _attr_icon = "mdi:account-multiple-check"
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArtemisCenterAvailabilityCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "artemis_available_personnel"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.available

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        return {
            "centre": data.unit_id,
            "en_intervention": data.in_operation,
        }
