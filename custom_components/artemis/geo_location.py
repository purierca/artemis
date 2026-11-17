"""Persistent ARTEMIS intervention archive exposed as geolocation events."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util.location import distance

from . import ArtemisRuntimeData
from .const import (
    CONF_GEOCODE_ADDRESSES,
    CONF_MAP_HISTORY_DAYS,
    DEFAULT_GEOCODE_ADDRESSES,
    DEFAULT_MAP_HISTORY_DAYS,
    DOMAIN,
    MAX_MAP_HISTORY_DAYS,
    MIN_MAP_HISTORY_DAYS,
)
from .helpers import (
    intervention_icon,
    intervention_is_recent,
    intervention_marker_kind,
    merge_intervention_archive,
    navigation_uri,
    normalize_geocoding_query,
    parse_geocoding_result,
)
from .marker import marker_picture_url
from .models import OperationsSnapshot

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = f"{DOMAIN}.intervention_history"
_GEOCODING_URL = "https://data.geopf.fr/geocodage/search"


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
        self._geocode_cache: dict[str, dict[str, Any]] = {}
        self._entities: dict[str, ArtemisInterventionGeoLocation] = {}
        self._lock = asyncio.Lock()
        self._local_tz = ZoneInfo(hass.config.time_zone)
        try:
            configured_days = int(
                entry.options.get(
                    CONF_MAP_HISTORY_DAYS,
                    DEFAULT_MAP_HISTORY_DAYS,
                )
            )
        except (TypeError, ValueError):
            configured_days = DEFAULT_MAP_HISTORY_DAYS
        self._map_history_days = max(
            MIN_MAP_HISTORY_DAYS,
            min(MAX_MAP_HISTORY_DAYS, configured_days),
        )
        self._map_history_window = timedelta(days=self._map_history_days)
        self._initialized = False

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

        raw_geocode_cache = stored.get("geocode_cache")
        if isinstance(raw_geocode_cache, dict):
            self._geocode_cache = {
                str(key): value
                for key, value in raw_geocode_cache.items()
                if isinstance(value, dict)
            }

        await self._async_ingest_snapshot(self.runtime.operations.data)
        await self._async_repair_recent_missing_coordinates()
        await self._async_geocode_missing_coordinates()

        now = datetime.now(UTC)
        entities = []
        for key, record in self._records.items():
            if not self._record_is_recent(record, now):
                continue
            entity = ArtemisInterventionGeoLocation(
                self.hass,
                self.entry.entry_id,
                key,
                record,
            )
            self._entities[key] = entity
            entities.append(entity)

        if entities:
            self._async_add_entities(entities)

        self._initialized = True
        _LOGGER.debug(
            "ARTEMIS intervention archive restored entries=%s active=%s "
            "map_feed=%s window_days=%s storage=local phone_id_logged=false "
            "operational_payload_logged=false",
            len(self._records),
            sum(1 for record in self._records.values() if record.get("active")),
            len(self._entities),
            self._map_history_days,
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
            await self._async_save_store()

        _LOGGER.debug(
            "ARTEMIS archived GPS recovery candidates=%s repaired=%s "
            "operational_payload_logged=false",
            min(len(candidates), 5),
            repaired,
        )

    async def _async_save_store(self) -> None:
        """Persist intervention history and the local geocoding cache."""

        await self._store.async_save(
            {
                "interventions": self._records,
                "geocode_cache": self._geocode_cache,
            }
        )

    def _record_is_recent(
        self,
        record: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """Return whether a stored intervention belongs in the map feed."""

        return intervention_is_recent(
            record,
            now or datetime.now(UTC),
            self._map_history_window,
            default_tz=self._local_tz,
        )

    @property
    def _geocoding_enabled(self) -> bool:
        return bool(
            self.entry.options.get(
                CONF_GEOCODE_ADDRESSES,
                DEFAULT_GEOCODE_ADDRESSES,
            )
        )

    async def _async_geocode_missing_coordinates(self) -> None:
        """Geocode archived address-only interventions when explicitly enabled."""

        if not self._geocoding_enabled:
            return

        now = datetime.now(UTC)
        recent_keys = tuple(
            key
            for key, record in self._records.items()
            if self._record_is_recent(record, now)
        )
        changed, cache_changed = await self._async_geocode_records(recent_keys)
        if changed or cache_changed:
            await self._async_save_store()

        _LOGGER.debug(
            "ARTEMIS address geocoding archive_scan=%s repaired=%s "
            "cache_entries=%s address_logged=false",
            len(self._records),
            len(changed),
            len(self._geocode_cache),
        )

    async def _async_geocode_records(
        self,
        keys: tuple[str, ...] | set[str],
    ) -> tuple[set[str], bool]:
        """Apply cached or live address geocoding to records without GPS."""

        if not self._geocoding_enabled:
            return set(), False

        changed: set[str] = set()
        cache_changed = False
        for key in keys:
            record = self._records.get(key)
            if record is None:
                continue
            if (
                record.get("latitude") is not None
                and record.get("longitude") is not None
            ):
                continue

            address = str(record.get("address") or "").strip()
            if not address or address == "Adresse non communiquee":
                continue

            result, new_cache_value = await self._async_geocode_address(address)
            cache_changed = cache_changed or new_cache_value
            if result is None:
                continue

            record["latitude"] = result["latitude"]
            record["longitude"] = result["longitude"]
            record["navigation_uri"] = navigation_uri(
                result["latitude"],
                result["longitude"],
                address,
            )
            record["geolocation_source"] = "geocoded_address"
            record["geocode_score"] = result["score"]
            record["geocode_label"] = result["label"]
            self._records[key] = record
            changed.add(key)

        return changed, cache_changed

    async def _async_geocode_address(
        self,
        address: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Resolve one French address through the official Geoplateforme API."""

        query = normalize_geocoding_query(address)
        if not query:
            return None, False

        cached = self._geocode_cache.get(query)
        if cached is not None:
            if not cached.get("found"):
                return None, False
            return cached, False

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                _GEOCODING_URL,
                params={
                    "q": query,
                    "index": "address",
                    "limit": "1",
                    "autocomplete": "0",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "ARTEMIS address geocoding HTTP status=%s "
                        "address_logged=false",
                        response.status,
                    )
                    return None, False
                payload = await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug(
                "ARTEMIS address geocoding unavailable error_type=%s "
                "address_logged=false",
                type(err).__name__,
            )
            return None, False

        latitude, longitude, score, label = parse_geocoding_result(payload)
        if latitude is None or longitude is None or score is None:
            self._geocode_cache[query] = {"found": False}
            return None, True

        result = {
            "found": True,
            "latitude": latitude,
            "longitude": longitude,
            "score": round(score, 3),
            "label": label,
        }
        self._geocode_cache[query] = result
        return result, True

    @callback
    def handle_coordinator_update(self) -> None:
        """Schedule archive synchronization after an operations refresh."""

        self.hass.async_create_task(
            self._async_ingest_snapshot(self.runtime.operations.data),
            "ARTEMIS intervention archive update",
        )

    async def _async_sync_geo_entities(
        self,
        changed: set[str],
        now: datetime,
    ) -> None:
        """Keep the temporary Home Assistant geo feed aligned with the configured window."""

        expired = [
            key
            for key in self._entities
            if not self._record_is_recent(self._records[key], now)
        ]
        for key in expired:
            entity = self._entities.pop(key)
            if entity.hass is not None:
                await entity.async_remove()

        new_entities: list[ArtemisInterventionGeoLocation] = []
        for key in changed:
            record = self._records[key]
            if not self._record_is_recent(record, now):
                continue

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

    async def _async_ingest_snapshot(
        self,
        snapshot: OperationsSnapshot | None,
    ) -> None:
        operations = snapshot.operations if snapshot is not None else ()
        now = datetime.now(UTC)
        observed_at = now.isoformat()

        async with self._lock:
            changed, new = merge_intervention_archive(
                self._records,
                operations,
                observed_at,
            )
            geocoded, cache_changed = await self._async_geocode_records(changed)
            changed.update(geocoded)

            if changed or cache_changed:
                await self._async_save_store()

            if self._initialized:
                await self._async_sync_geo_entities(changed, now)

            if not changed and not cache_changed:
                return

            _LOGGER.debug(
                "ARTEMIS intervention archive updated total=%s active=%s changed=%s "
                "new=%s map_feed=%s window_days=%s storage=local "
                "operational_payload_logged=false",
                len(self._records),
                sum(1 for record in self._records.values() if record.get("active")),
                len(changed),
                len(new),
                len(self._entities),
                self._map_history_days,
            )


class ArtemisInterventionGeoLocation(GeolocationEvent):
    """One recent archived intervention exposed as a Home Assistant map event."""

    _attr_source = DOMAIN
    _attr_should_poll = False
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        archive_key: str,
        record: dict[str, Any],
    ) -> None:
        digest = hashlib.sha256(archive_key.encode()).hexdigest()[:16]
        self.entity_id = f"geo_location.artemis_{digest}"
        self._attr_unique_id = f"artemis_intervention_{digest}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_interventions")},
            name="ARTEMIS / Smartemis — Interventions",
            manufacturer="ARTEMIS / Smartemis",
            model="Intervention map feed",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._home_latitude = hass.config.latitude
        self._home_longitude = hass.config.longitude
        self._record: dict[str, Any] = {}
        self.update_record(record)

    def update_record(self, record: dict[str, Any]) -> None:
        """Update the retained intervention without changing its entity id."""

        self._record = dict(record)
        number = str(record.get("number") or record.get("id") or "").strip()
        title = str(record.get("title") or "Intervention").strip()
        marker_kind = intervention_marker_kind(title, record.get("type_code"))
        self._attr_icon = intervention_icon(title, record.get("type_code"))
        self._attr_entity_picture = marker_picture_url(
            marker_kind,
            bool(record.get("active")),
        )
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
