"""Persistent ARTEMIS intervention archive exposed as geolocation events."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util.location import distance

from . import ArtemisRuntimeData
from .const import DOMAIN
from .helpers import merge_intervention_archive, navigation_uri
from .models import OperationsSnapshot

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = f"{DOMAIN}.intervention_history"


async def async_remove_history_store(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Delete the locally persisted intervention archive for a removed entry."""

    store: Store[dict[str, Any]] = Store(
        hass,
        _STORAGE_VERSION,
        f"{_STORAGE_KEY_PREFIX}.{entry_id}",
    )
    await store.async_remove()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Restore the local intervention archive and follow live updates."""

    runtime: ArtemisRuntimeData = entry.runtime_data
    manager = ArtemisInterventionHistoryManager(
        hass,
        entry,
        runtime,
        async_add_entities,
    )
    await manager.async_initialize()
    entry.async_on_unload(
        runtime.operations.async_add_listener(manager.handle_coordinator_update)
    )


class ArtemisInterventionHistoryManager:
    """Persist every intervention observed by this Home Assistant instance."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: ArtemisRuntimeData,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.runtime = runtime
        self._async_add_entities = async_add_entities
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        self._records: dict[str, dict[str, Any]] = {}
        self._entities: dict[str, ArtemisInterventionGeoLocation] = {}
        self._lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        """Load local history, merge the current snapshot and create entities."""

        stored = await self._store.async_load() or {}
        raw_records = stored.get("interventions")
        if isinstance(raw_records, dict):
            self._records = {
                str(key): value
                for key, value in raw_records.items()
                if isinstance(value, dict)
            }

        await self._async_ingest_snapshot(self.runtime.operations.data)
        await self._async_repair_recent_missing_coordinates()

        entities = []
        for key, record in self._records.items():
            entity = ArtemisInterventionGeoLocation(self.hass, key, record)
            self._entities[key] = entity
            entities.append(entity)

        if entities:
            self._async_add_entities(entities)

        _LOGGER.debug(
            "ARTEMIS intervention archive restored entries=%s active=%s "
            "storage=local phone_id_logged=false operational_payload_logged=false",
            len(self._records),
            sum(1 for record in self._records.values() if record.get("active")),
        )

    async def _async_repair_recent_missing_coordinates(self) -> None:
        """Best-effort repair for recent Smartemis archive entries missing GPS."""

        recover = getattr(self.runtime.operations, "async_recover_coordinates", None)
        if recover is None:
            return

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        candidates: list[tuple[str, dict[str, Any]]] = []
        for key, record in self._records.items():
            if record.get("latitude") is not None and record.get("longitude") is not None:
                continue
            if record.get("active"):
                continue

            ended_raw = record.get("ended") or record.get("last_seen")
            if not ended_raw:
                continue
            try:
                ended = datetime.fromisoformat(str(ended_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=UTC)
            if ended < cutoff:
                continue

            candidates.append((key, record))

        repaired = 0
        for key, record in candidates[:5]:
            try:
                intervention_id = int(record.get("id"))
            except (TypeError, ValueError):
                continue

            latitude, longitude = await recover(intervention_id)
            if latitude is None or longitude is None:
                continue

            record["latitude"] = latitude
            record["longitude"] = longitude
            record["navigation_uri"] = navigation_uri(
                latitude,
                longitude,
                str(record.get("address") or ""),
            )
            self._records[key] = record
            repaired += 1

        if repaired:
            await self._store.async_save({"interventions": self._records})

        _LOGGER.debug(
            "ARTEMIS archived GPS recovery candidates=%s repaired=%s "
            "operational_payload_logged=false",
            min(len(candidates), 5),
            repaired,
        )

    @callback
    def handle_coordinator_update(self) -> None:
        """Schedule archive synchronization after an operations refresh."""

        self.hass.async_create_task(
            self._async_ingest_snapshot(self.runtime.operations.data),
            "ARTEMIS intervention archive update",
        )

    async def _async_ingest_snapshot(
        self,
        snapshot: OperationsSnapshot | None,
    ) -> None:
        operations = snapshot.operations if snapshot is not None else ()
        observed_at = datetime.now(UTC).isoformat()

        async with self._lock:
            changed, new = merge_intervention_archive(
                self._records,
                operations,
                observed_at,
            )
            if not changed:
                return

            await self._store.async_save({"interventions": self._records})

            new_entities: list[ArtemisInterventionGeoLocation] = []
            for key in changed:
                record = self._records[key]
                if entity := self._entities.get(key):
                    entity.update_record(record)
                    if entity.hass is not None:
                        entity.async_write_ha_state()
                    continue

                entity = ArtemisInterventionGeoLocation(self.hass, key, record)
                self._entities[key] = entity
                new_entities.append(entity)

            if new_entities:
                self._async_add_entities(new_entities)

            _LOGGER.debug(
                "ARTEMIS intervention archive updated total=%s active=%s changed=%s "
                "new=%s storage=local operational_payload_logged=false",
                len(self._records),
                sum(1 for record in self._records.values() if record.get("active")),
                len(changed),
                len(new),
            )


class ArtemisInterventionGeoLocation(GeolocationEvent):
    """One intervention retained as a local Home Assistant map event."""

    _attr_source = DOMAIN
    _attr_icon = "mdi:fire-alert"
    _attr_should_poll = False
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(
        self,
        hass: HomeAssistant,
        archive_key: str,
        record: dict[str, Any],
    ) -> None:
        digest = hashlib.sha256(archive_key.encode()).hexdigest()[:16]
        self.entity_id = f"geo_location.artemis_{digest}"
        self._home_latitude = hass.config.latitude
        self._home_longitude = hass.config.longitude
        self._record: dict[str, Any] = {}
        self.update_record(record)

    def update_record(self, record: dict[str, Any]) -> None:
        """Update the retained intervention without changing its entity id."""

        self._record = dict(record)
        number = str(record.get("number") or record.get("id") or "").strip()
        title = str(record.get("title") or "Intervention").strip()
        self._attr_name = (
            f"ARTEMIS {number} — {title}" if number else f"ARTEMIS — {title}"
        )

        self._attr_latitude = _float_or_none(record.get("latitude"))
        self._attr_longitude = _float_or_none(record.get("longitude"))
        self._attr_distance = None
        if self._attr_latitude is not None and self._attr_longitude is not None:
            meters = distance(
                self._home_latitude,
                self._home_longitude,
                self._attr_latitude,
                self._attr_longitude,
            )
            if meters is not None:
                self._attr_distance = meters / 1000

        self._attr_extra_state_attributes = {
            key: value
            for key, value in record.items()
            if key not in {"latitude", "longitude"}
        }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
