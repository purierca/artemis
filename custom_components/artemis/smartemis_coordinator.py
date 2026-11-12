"""Home Assistant coordinators for the Smartemis backend."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SMARTEMIS_UPDATE_INTERVAL
from .helpers import coerce_coordinate, cycle_status_code, navigation_uri
from .models import OperationsSnapshot, PlanningSnapshot, StatusValue
from .smartemis import (
    RIGHT_PARAMETERS,
    YES,
    SmartemisApi,
    SmartemisCenter,
    SmartemisConnectionError,
    SmartemisServiceError,
    SmartemisUserContext,
)

_LOGGER = logging.getLogger(__name__)


def _parse_datetime(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _status_value(value: Any) -> StatusValue | None:
    if not isinstance(value, dict):
        return None
    code = str(value.get("code") or "").strip()
    if not code:
        return None
    return StatusValue(code=code, name=str(value.get("name") or code).strip())


class SmartemisPlanningCoordinator(DataUpdateCoordinator[PlanningSnapshot]):
    """Expose and safely update the subscriber's current Smartemis planning."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartemisApi,
        server: str,
        phone_id: str,
        center: SmartemisCenter,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smartemis personal planning",
            config_entry=entry,
            update_interval=SMARTEMIS_UPDATE_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.server = server
        self.phone_id = phone_id
        self.center = center
        self.unit_id = center.id
        self.staff_id = center.person_id or center.matricule
        self.staff_name = center.person_name
        self.read_only = True
        self._tz = ZoneInfo(hass.config.time_zone)
        self._allowed_statuses: dict[str, dict[str, Any]] = {}
        self._current_period_end_raw: str | None = None
        self._current_period_end: datetime | None = None
        self._write_lock = asyncio.Lock()

    @property
    def effective_next_change(self) -> datetime | None:
        return self.data.next_change if self.data else None

    @property
    def effective_next_status(self) -> StatusValue | None:
        return self.data.next_status if self.data else None

    @property
    def effective_override_boundary(self) -> datetime | None:
        """Return the Smartemis EOP boundary used by a status write."""

        return self._current_period_end

    @property
    def status_override_active(self) -> bool:
        """Smartemis writes target the server current period, not a local override."""

        return False

    def is_status_code_available(self, code: str) -> bool:
        """Return whether Smartemis explicitly allows a target status."""

        return code in self._allowed_statuses

    async def async_cycle_status(self) -> str:
        """Cycle IND -> DI1 -> AS1 -> IND through updateCurrentPlanning."""

        async with self._write_lock:
            # Always generate the write from a fresh server state.
            await self.async_request_refresh()
            if self.data is None or self.data.current is None:
                raise HomeAssistantError("Smartemis current status is unavailable")

            current_code = self.data.current.code
            target_code = cycle_status_code(current_code)
            if target_code is None:
                raise HomeAssistantError(
                    f"Smartemis status {current_code} is not in the IND/DI1/AS1 cycle"
                )

            target_status = self._allowed_statuses.get(target_code)
            if target_status is None:
                raise HomeAssistantError(
                    f"Smartemis does not allow status {target_code} for this planning"
                )
            if not self._current_period_end_raw:
                raise HomeAssistantError(
                    "Smartemis did not expose the end of the current planning period"
                )

            _LOGGER.debug(
                "Smartemis status write start center=%s current=%s target=%s "
                "boundary=EOP boundary_time=%s phone_id_logged=false",
                self.center.id,
                current_code,
                target_code,
                self._current_period_end_raw,
            )
            try:
                await self.api.async_update_current_planning(
                    self.server,
                    phone_id=self.phone_id,
                    center_id=self.center.id,
                    matricule=self.center.matricule,
                    status=target_status,
                    period_end=self._current_period_end_raw,
                )
            except (SmartemisConnectionError, SmartemisServiceError) as err:
                _LOGGER.debug(
                    "Smartemis status write failed center=%s current=%s target=%s "
                    "error_type=%s message=%s",
                    self.center.id,
                    current_code,
                    target_code,
                    type(err).__name__,
                    str(err),
                )
                raise HomeAssistantError(str(err)) from err

            observed: str | None = None
            for attempt, delay in enumerate((0.0, 0.5, 1.0), start=1):
                if delay:
                    await asyncio.sleep(delay)
                await self.async_request_refresh()
                observed = (
                    self.data.current.code
                    if self.data is not None and self.data.current is not None
                    else None
                )
                _LOGGER.debug(
                    "Smartemis status write read-back center=%s attempt=%s "
                    "requested=%s observed=%s",
                    self.center.id,
                    attempt,
                    target_code,
                    observed,
                )
                if observed == target_code:
                    return target_code

            raise HomeAssistantError(
                "Smartemis acknowledged the status write but read-back did not "
                f"confirm {target_code}; observed {observed or 'unknown'}"
            )

    async def _async_update_data(self) -> PlanningSnapshot:
        try:
            payload = await self.api.async_get_current_planning(
                self.server,
                phone_id=self.phone_id,
                center_id=self.center.id,
                matricule=self.center.matricule,
            )
        except SmartemisServiceError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartemisConnectionError as err:
            raise UpdateFailed(str(err)) from err

        current_period = payload.get("current_period") or {}
        current = _status_value(current_period.get("status"))
        self._current_period_end_raw = (
            str(current_period.get("end")).strip()
            if current_period.get("end")
            else None
        )
        self._current_period_end = _parse_datetime(
            self._current_period_end_raw, self._tz
        )
        self._allowed_statuses = {
            str(status.get("code")).strip(): status
            for status in (payload.get("allowed_statuses") or ())
            if isinstance(status, dict) and status.get("code")
        }
        # If the server does not advertise writable statuses, fail closed.
        self.read_only = not bool(self._allowed_statuses)

        if current is None and self.center.administrative_status_code:
            current = StatusValue(
                self.center.administrative_status_code,
                self.center.administrative_status_name
                or self.center.administrative_status_code,
            )

        _LOGGER.debug(
            "Smartemis planning refreshed center=%s current=%s allowed_statuses=%s "
            "read_only=%s eop=%s phone_id_logged=false",
            self.center.id,
            current.code if current else None,
            sorted(self._allowed_statuses),
            self.read_only,
            self._current_period_end_raw,
        )

        now = datetime.now(self._tz)
        current_since = _parse_datetime(current_period.get("start"), self._tz)

        next_change = None
        next_status = None
        candidates = []
        for period in payload.get("periods") or ():
            status = _status_value(period.get("status"))
            start = _parse_datetime(period.get("start"), self._tz)
            if status and start and start > now:
                candidates.append((start, status))
        for start, status in sorted(candidates, key=lambda item: item[0]):
            if current is None or status.code != current.code:
                next_change = start
                next_status = status
                break

        available = payload.get("available_personnel")
        try:
            available_personnel = int(available) if available is not None else None
        except (TypeError, ValueError):
            available_personnel = None

        return PlanningSnapshot(
            current=current,
            current_since=current_since,
            next_status=next_status,
            next_change=next_change,
            staff_name=self.staff_name,
            staff_id=self.staff_id,
            unit_id=self.unit_id,
            available_personnel=available_personnel,
        )


class SmartemisOperationsCoordinator(DataUpdateCoordinator[OperationsSnapshot]):
    """Poll the Smartemis intervention view exposed to the subscriber."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartemisApi,
        server: str,
        phone_id: str,
        center: SmartemisCenter,
        context: SmartemisUserContext,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smartemis operations",
            config_entry=entry,
            update_interval=SMARTEMIS_UPDATE_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.server = server
        self.phone_id = phone_id
        self.center = center
        self.context = context

    async def _async_update_data(self) -> OperationsSnapshot:
        global_view = (
            RIGHT_PARAMETERS in self.center.rights
            and self.context.display_interventions == YES
        )
        _LOGGER.debug(
            "Smartemis interventions refresh start center=%s global_view=%s "
            "display_level=%s phone_id_logged=false",
            self.center.id,
            global_view,
            self.context.display_level if global_view else None,
        )
        try:
            summaries = await self.api.async_get_interventions(
                self.server,
                phone_id=self.phone_id,
                center_id=None if global_view else self.center.id,
                matricule=None if global_view else self.center.matricule,
                display_level=self.context.display_level if global_view else None,
            )
        except SmartemisServiceError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartemisConnectionError as err:
            raise UpdateFailed(str(err)) from err

        operations = []
        detail_success = 0
        detail_failure = 0
        for summary in summaries:
            detail: dict[str, Any] = {}
            try:
                intervention_id = int(summary.get("id"))
            except (TypeError, ValueError):
                intervention_id = None

            if intervention_id is not None:
                try:
                    detail = await self.api.async_get_intervention_detail(
                        self.server,
                        phone_id=self.phone_id,
                        center_id=self.center.id,
                        matricule=self.center.matricule,
                        intervention_id=intervention_id,
                    )
                    detail_success += 1
                except (SmartemisConnectionError, SmartemisServiceError) as err:
                    detail_failure += 1
                    _LOGGER.debug(
                        "Smartemis intervention detail unavailable error_type=%s",
                        type(err).__name__,
                    )

            operations.append(_operation_snapshot(summary, detail))

        operations.sort(key=lambda item: item["id"])
        _LOGGER.debug(
            "Smartemis interventions refresh complete active=%s details_ok=%s "
            "details_failed=%s operational_payload_logged=false",
            len(operations),
            detail_success,
            detail_failure,
        )
        return OperationsSnapshot(count=len(operations), operations=tuple(operations))


def _operation_snapshot(
    summary: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    addresses = detail.get("addresses") or ()
    first_address = addresses[0] if addresses else {}
    address = str(
        first_address.get("address")
        or summary.get("address")
        or "Adresse non communiquee"
    ).strip()
    latitude = coerce_coordinate(first_address.get("latitude"), latitude=True)
    longitude = coerce_coordinate(first_address.get("longitude"), latitude=False)

    vehicles = [
        {
            "center": str(vehicle.get("center_name") or vehicle.get("center_id") or ""),
            "name": str(vehicle.get("name") or vehicle.get("id") or "Engin"),
            "state_code": str(vehicle.get("state_code") or ""),
            "state_name": "",
        }
        for vehicle in (detail.get("vehicles") or ())
        if isinstance(vehicle, dict)
    ]
    vehicles.sort(key=lambda item: (item["center"], item["name"]))

    operation_id = str(summary.get("id") or summary.get("number") or "unknown")
    return {
        "id": operation_id,
        "number": str(summary.get("number") or operation_id),
        "title": str(summary.get("title") or "Intervention").strip(),
        "created": summary.get("created"),
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "navigation_uri": navigation_uri(latitude, longitude, address),
        "state_code": "",
        "state_name": "",
        "vehicles": vehicles,
    }
