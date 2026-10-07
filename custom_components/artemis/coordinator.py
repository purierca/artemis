"""Data coordinators for ARTEMIS WebEvo."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ArtemisApi,
    ArtemisAuthError,
    ArtemisConnectionError,
    ArtemisWriteError,
)
from .const import (
    CENTER_COUNTER_UPDATE_INTERVAL,
    MAX_LOOKAHEAD_WEEKS,
    OPERATIONS_UPDATE_INTERVAL,
    PLANNING_UPDATE_INTERVAL,
)
from .helpers import (
    build_planning_snapshot,
    build_status_override_requests,
    cycle_status_code,
    normalize_planning,
    operation_snapshot_data,
    parse_clock,
    week_start_for,
)
from .models import (
    CenterAvailabilitySnapshot,
    OperationsSnapshot,
    PlanningSnapshot,
    StatusValue,
)

_LOGGER = logging.getLogger(__name__)

_OVERRIDE_STORE_VERSION = 1


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
            always_update=False,
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
        self._payloads: list[dict] = []
        self._available_status_codes: set[str] = set()
        self.read_only = bool(init_data.get("readOnly", True))
        self._write_lock = asyncio.Lock()

        self._override_store = Store[dict[str, str]](
            hass,
            _OVERRIDE_STORE_VERSION,
            f"artemis.{entry.entry_id}.status_override",
        )
        self._override_boundary: datetime | None = None
        self._override_next_status: StatusValue | None = None

    async def async_initialize_status_override(self) -> None:
        """Restore a pending local override boundary across HA restarts."""
        stored = await self._override_store.async_load() or {}
        raw_boundary = stored.get("boundary")
        if not raw_boundary:
            return
        try:
            boundary = datetime.fromisoformat(raw_boundary)
            if boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=self._tz)
            boundary = boundary.astimezone(self._tz)
        except (TypeError, ValueError):
            await self._override_store.async_save({})
            return

        if boundary <= datetime.now(self._tz):
            await self._override_store.async_save({})
            return

        code = str(stored.get("next_code") or "").strip()
        name = str(stored.get("next_name") or code).strip()
        if not code:
            await self._override_store.async_save({})
            return

        self._override_boundary = boundary
        self._override_next_status = StatusValue(code=code, name=name)

    def _override_is_active(self, now: datetime | None = None) -> bool:
        if self._override_boundary is None or self._override_next_status is None:
            return False
        now = now or datetime.now(self._tz)
        return now < self._override_boundary

    @property
    def effective_next_change(self) -> datetime | None:
        """Return the preserved planned boundary while a manual override is active."""
        if self._override_is_active():
            return self._override_boundary
        return self.data.next_change if self.data is not None else None

    @property
    def effective_next_status(self) -> StatusValue | None:
        """Return the status originally planned at the preserved boundary."""
        if self._override_is_active():
            return self._override_next_status
        return self.data.next_status if self.data is not None else None

    @property
    def effective_override_boundary(self) -> datetime | None:
        """Return a safe boundary for a temporary status override.

        Prefer the next real status change. If no different future status is
        known, fall back to the end of the contiguous same-status planning
        horizon currently returned by ARTEMIS. This keeps the cycle button
        usable for an otherwise indefinite status while still never writing
        beyond planning data that WebEvo actually returned.
        """
        if self._override_is_active():
            return self._override_boundary
        if self.data is None:
            return None
        return self.data.next_change or self.data.current_period_end

    @property
    def status_override_active(self) -> bool:
        return self._override_is_active()

    def is_status_code_available(self, code: str) -> bool:
        """Return whether WebEvo advertised a status code for this personal planning."""
        return not self._available_status_codes or code in self._available_status_codes

    async def _set_override(
        self, boundary: datetime, next_status: StatusValue
    ) -> None:
        self._override_boundary = boundary
        self._override_next_status = next_status
        await self._override_store.async_save(
            {
                "boundary": boundary.isoformat(),
                "next_code": next_status.code,
                "next_name": next_status.name,
            }
        )

    async def _clear_override(self) -> None:
        self._override_boundary = None
        self._override_next_status = None
        await self._override_store.async_save({})

    async def async_cycle_status(self) -> str:
        """Cycle IND -> DI1 -> AS1 -> IND using the safest known boundary."""
        if self.read_only:
            raise HomeAssistantError("ARTEMIS reports this personal planning as read-only")

        async with self._write_lock:
            # Start from the freshest server state before generating a write payload.
            await self.async_request_refresh()
            if self.data is None or self.data.current is None:
                raise HomeAssistantError("ARTEMIS current status is unavailable")

            current_code = self.data.current.code
            target_code = cycle_status_code(current_code)
            if target_code is None:
                raise HomeAssistantError(
                    f"ARTEMIS status {current_code} is not in the IND/DI1/AS1 cycle"
                )
            if not self.is_status_code_available(target_code):
                raise HomeAssistantError(
                    f"ARTEMIS does not offer status {target_code} for this planning"
                )

            now = datetime.now(self._tz)
            # WebEvo's save endpoint works at minute precision. Apply from the
            # current minute so the new active status is visible immediately.
            start = now.replace(second=0, microsecond=0)

            had_override = self._override_is_active(now)
            preserve_planned_boundary = False
            planned_next: StatusValue | None = None

            if had_override:
                # A previous press already preserved a real ARTEMIS change. Keep
                # using that exact boundary across repeated presses.
                boundary = self._override_boundary
                planned_next = self._override_next_status
                preserve_planned_boundary = True
            elif self.data.next_change is not None and self.data.next_status is not None:
                # A real different status is planned: never overwrite it.
                boundary = self.data.next_change
                planned_next = self.data.next_status
                preserve_planned_boundary = True
            else:
                # No different future status is planned. WebEvo still exposes a
                # finite writable planning horizon. Change the current status
                # through that returned horizon, but do not invent a synthetic
                # "next status" in Home Assistant.
                boundary = self.data.current_period_end

            if boundary is None:
                raise HomeAssistantError(
                    "ARTEMIS did not expose a writable current planning window"
                )
            if start >= boundary:
                raise HomeAssistantError(
                    "The current ARTEMIS planning window is too close to its end to safely apply the change"
                )

            try:
                requests = build_status_override_requests(
                    self._payloads,
                    staff_id=self.staff_id,
                    start=start,
                    end=boundary,
                    new_status_code=target_code,
                    tz=self._tz,
                )
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err

            if not requests:
                raise HomeAssistantError(
                    "ARTEMIS returned no writable planning row for the override window"
                )

            new_override = preserve_planned_boundary and not had_override
            if new_override and planned_next is not None:
                # Preserve only a *real* originally planned status boundary. When
                # there is no future different status, the writable planning horizon
                # is merely a technical write limit and must not appear as a fake
                # next ARTEMIS change.
                await self._set_override(boundary, planned_next)

            saved_rows = 0
            try:
                for request in requests:
                    await self.api.async_save_staff(request)
                    saved_rows += 1
            except (
                ArtemisAuthError,
                ArtemisConnectionError,
                ArtemisWriteError,
            ) as err:
                # If nothing reached the server, discard the new boundary. If some
                # days were already saved, keep it so a retry still stops at the
                # originally planned change instead of extending the override.
                if new_override and saved_rows == 0:
                    await self._clear_override()
                await self.async_request_refresh()
                raise HomeAssistantError(str(err)) from err

            # WebEvo can acknowledge saveStaff before getPlanning reflects the
            # changed row. Refresh a few times so Home Assistant (and therefore the
            # persistent notification action label) normally sees the new current
            # code immediately instead of one polling cycle later.
            for delay in (0.0, 0.5, 1.0):
                if delay:
                    await asyncio.sleep(delay)
                await self.async_request_refresh()
                if (
                    self.data is not None
                    and self.data.current is not None
                    and self.data.current.code == target_code
                ):
                    break

            return target_code

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

                for state in payload.get("admStates") or []:
                    code = str((state or {}).get("code") or "").strip()
                    if code:
                        self._available_status_codes.add(code)

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

            self._payloads = payloads
            if self._override_boundary is not None and now >= self._override_boundary:
                await self._clear_override()

            schedule_when = (
                self._override_boundary
                if self._override_is_active(now)
                else snapshot.next_change
            )
            self._schedule_exact_change(schedule_when)
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
            always_update=False,
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
            return CenterAvailabilitySnapshot(
                available=int(payload.get("availableCounter") or 0),
                in_operation=int(payload.get("inOperationCounter") or 0),
                unit_id=self.planning.unit_id,
            )
        except ArtemisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArtemisConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except (TypeError, ValueError) as err:
            raise UpdateFailed("Invalid ARTEMIS centre counter response") from err


class ArtemisOperationsCoordinator(DataUpdateCoordinator[OperationsSnapshot]):
    """Poll the live ARTEMIS synoptic and expose one current snapshot."""

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
            update_interval=(
                timedelta(seconds=refresh_seconds)
                if refresh_seconds
                else OPERATIONS_UPDATE_INTERVAL
            ),
            always_update=False,
        )
        self.api = api

    async def _async_update_data(self) -> OperationsSnapshot:
        try:
            payload = await self.api.async_operations()
            operations = tuple(
                sorted(
                    (
                        operation_snapshot_data(operation)
                        for operation in (payload.get("operations") or [])
                        if isinstance(operation, dict)
                    ),
                    key=lambda operation: operation["id"],
                )
            )
            return OperationsSnapshot(count=len(operations), operations=operations)
        except ArtemisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArtemisConnectionError as err:
            raise UpdateFailed(str(err)) from err
