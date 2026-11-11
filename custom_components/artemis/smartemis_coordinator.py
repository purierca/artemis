"""Home Assistant coordinators for the read-only Smartemis backend."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SMARTEMIS_UPDATE_INTERVAL
from .helpers import coerce_coordinate, navigation_uri
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
    """Expose the subscriber's current Smartemis planning without writes."""

    read_only = True

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
        self._tz = ZoneInfo(hass.config.time_zone)

    @property
    def effective_next_change(self) -> datetime | None:
        return self.data.next_change if self.data else None

    @property
    def effective_next_status(self) -> StatusValue | None:
        return self.data.next_status if self.data else None

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
        if current is None and self.center.administrative_status_code:
            current = StatusValue(
                self.center.administrative_status_code,
                self.center.administrative_status_name
                or self.center.administrative_status_code,
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
                except (SmartemisConnectionError, SmartemisServiceError):
                    pass

            operations.append(_operation_snapshot(summary, detail))

        operations.sort(key=lambda item: item["id"])
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
