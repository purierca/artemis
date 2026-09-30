"""Data coordinators for ARTEMIS WebEvo."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ArtemisApi, ArtemisAuthError, ArtemisConnectionError
from .const import (
    CENTER_COUNTER_UPDATE_INTERVAL,
    EVENT_NEW_INTERVENTION,
    MAX_LOOKAHEAD_WEEKS,
    OPERATIONS_UPDATE_INTERVAL,
    PLANNING_UPDATE_INTERVAL,
)
from .helpers import (
    build_planning_snapshot,
    normalize_planning,
    operation_event_data,
    operation_identifier,
    parse_clock,
    week_start_for,
)
from .models import CenterAvailabilitySnapshot, OperationsSnapshot, PlanningSnapshot

_LOGGER = logging.getLogger(__name__)


class ArtemisPlanningCoordinator(DataUpdateCoordinator[PlanningSnapshot]):
    """Coordinate personal planning data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ArtemisApi,
        init_data: dict,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ARTEMIS personal planning",
            config_entry=entry,
            update_interval=PLANNING_UPDATE_INTERVAL,
        )
        self.api = api
        self.init_data = init_data
        self.unit_id = str(init_data.get("fireunitId") or api.unit_id or "")
        self.staff_id = str(init_data.get("staffId") or "")
        staffs = init_data.get("staffs") or []
        self.staff_name = ""
        for staff in staffs:
            if str(staff.get("code")) == self.staff_id:
                self.staff_name = str(staff.get("name") or "")
                break
        self._unsub_change = None
        self._tz = ZoneInfo(hass.config.time_zone)
        self._planning_start = parse_clock(None)
        self._planning_ids: dict[str, int | str] = {}

    async def _async_update_data(self) -> PlanningSnapshot:
        try:
            now = datetime.now(self._tz)
            planning_start = parse_clock(None)
            first_week = week_start_for(now, planning_start)
            payloads: list[dict] = []
            lookahead = 0
            snapshot: PlanningSnapshot | None = None

            for index in range(MAX_LOOKAHEAD_WEEKS):
                week = first_week + timedelta(days=7 * index)
                payload = await self.api.async_person_planning(
                    self.unit_id,
                    self.staff_id,
                    week.isoformat(),
                )
                payloads.append(payload)
                lookahead = index + 1

                # Use the server's actual planning-start time as soon as we have it.
                planning = payload.get("planning") or {}
                fire_unit = planning.get("fireUnit") or {}
                if fire_unit.get("planningStartTime"):
                    planning_start = parse_clock(fire_unit.get("planningStartTime"))
                    self._planning_start = planning_start

                for staff_day in planning.get("staff") or []:
                    planning_date = staff_day.get("planningStartDate")
                    planning_id = staff_day.get("planningId")
                    if planning_date and planning_id not in (None, ""):
                        self._planning_ids[str(planning_date)] = planning_id

                intervals = normalize_planning(payloads, self._tz)
                snapshot = build_planning_snapshot(
                    intervals,
                    now,
                    staff_name=self.staff_name,
                    staff_id=self.staff_id,
                    unit_id=self.unit_id,
                    lookahead_weeks=lookahead,
                )
                if snapshot.current is not None and snapshot.next_change is not None:
                    break

            if snapshot is None:
                raise UpdateFailed("ARTEMIS returned no planning data")

            self._schedule_exact_change(snapshot.next_change)
            return snapshot

        except ArtemisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArtemisConnectionError as err:
            raise UpdateFailed(str(err)) from err

    @callback
    def _schedule_exact_change(self, when: datetime | None) -> None:
        """Refresh just after the next planned boundary so status changes on time."""
        if self._unsub_change:
            self._unsub_change()
            self._unsub_change = None
        if when is None:
            return

        target = when + timedelta(seconds=1)
        if target <= dt_util.now():
            return

        @callback
        def _handle_change(_now: datetime) -> None:
            self._unsub_change = None
            self.hass.async_create_task(self.async_request_refresh())

        self._unsub_change = async_track_point_in_time(self.hass, _handle_change, target)

    def planning_id_for(self, moment: datetime) -> int | str | None:
        """Return the centre planning id for the operational date containing moment."""
        local = moment.astimezone(self._tz)
        operational_date = local.date()
        if local.timetz().replace(tzinfo=None) < self._planning_start:
            operational_date -= timedelta(days=1)
        return self._planning_ids.get(operational_date.isoformat())

    @callback
    def async_shutdown_artemis(self) -> None:
        if self._unsub_change:
            self._unsub_change()
            self._unsub_change = None


class ArtemisCenterAvailabilityCoordinator(
    DataUpdateCoordinator[CenterAvailabilitySnapshot]
):
    """Coordinate native ARTEMIS centre availability counters."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ArtemisApi,
        planning: ArtemisPlanningCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ARTEMIS centre availability",
            config_entry=entry,
            update_interval=CENTER_COUNTER_UPDATE_INTERVAL,
        )
        self.api = api
        self.planning = planning
        self._tz = ZoneInfo(hass.config.time_zone)

    async def _async_update_data(self) -> CenterAvailabilitySnapshot:
        try:
            now = datetime.now(self._tz)
            planning_id = self.planning.planning_id_for(now)
            if planning_id is None:
                await self.planning.async_request_refresh()
                planning_id = self.planning.planning_id_for(now)
            if planning_id is None:
                raise UpdateFailed(
                    "ARTEMIS did not expose a planning id for the current operational day"
                )

            # WebEvo sends the selected local wall-clock value with a trailing Z.
            # Reproduce that format rather than converting the wall clock to UTC.
            date_value = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            payload = await self.api.async_planning_counters(
                planning_id,
                self.planning.unit_id,
                date_value,
            )
            counters = tuple(
                item
                for item in (payload.get("counters") or [])
                if isinstance(item, dict)
            )
            return CenterAvailabilitySnapshot(
                available=int(payload.get("availableCounter") or 0),
                in_operation=int(payload.get("inOperationCounter") or 0),
                counters=counters,
                unit_id=self.planning.unit_id,
            )
        except ArtemisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArtemisConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except (TypeError, ValueError) as err:
            raise UpdateFailed("Invalid ARTEMIS centre counter response") from err


class ArtemisOperationsCoordinator(DataUpdateCoordinator[OperationsSnapshot]):
    """Coordinate live ARTEMIS operations and emit one event per new operation."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ArtemisApi,
        *,
        refresh_seconds: int = 15,
    ) -> None:
        refresh_seconds = max(15, refresh_seconds)
        super().__init__(
            hass,
            _LOGGER,
            name="ARTEMIS operations",
            config_entry=entry,
            update_interval=timedelta(seconds=refresh_seconds) if refresh_seconds else OPERATIONS_UPDATE_INTERVAL,
        )
        self.api = api
        self._seeded = False
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=2000)

    def _remember(self, operation_id: str) -> None:
        if operation_id in self._seen:
            return
        if len(self._seen_order) == self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self._seen.discard(oldest)
        self._seen_order.append(operation_id)
        self._seen.add(operation_id)

    async def _async_update_data(self) -> OperationsSnapshot:
        try:
            payload = await self.api.async_operations()
            operations = tuple(
                operation
                for operation in (payload.get("operations") or [])
                if isinstance(operation, dict)
            )

            if not self._seeded:
                for operation in operations:
                    self._remember(operation_identifier(operation))
                self._seeded = True
            else:
                for operation in operations:
                    operation_id = operation_identifier(operation)
                    if operation_id in self._seen:
                        continue
                    self._remember(operation_id)
                    self.hass.bus.async_fire(
                        EVENT_NEW_INTERVENTION,
                        operation_event_data(operation),
                    )

            return OperationsSnapshot(count=len(operations), operations=operations)

        except ArtemisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArtemisConnectionError as err:
            raise UpdateFailed(str(err)) from err
