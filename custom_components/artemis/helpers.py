"""Pure helpers for ARTEMIS WebEvo data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .const import STATUS_CYCLE
from .models import PlanningInterval, PlanningSnapshot, StatusValue


def parse_hms(value: str | None) -> timedelta:
    """Parse HH:MM:SS to a timedelta."""
    if not value:
        return timedelta(0)
    parts = value.split(":")
    if len(parts) != 3:
        return timedelta(0)
    hours, minutes, seconds = (int(part) for part in parts)
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def parse_clock(value: str | None, default: time = time(7, 0)) -> time:
    """Parse an ARTEMIS HH:MM:SS clock value."""
    if not value:
        return default
    try:
        return time.fromisoformat(value)
    except ValueError:
        return default


def week_start_for(moment: datetime, planning_start: time = time(7, 0)) -> date:
    """Return the operational ISO-week Monday that contains moment."""
    operational_date = moment.date()
    if moment.timetz().replace(tzinfo=None) < planning_start:
        operational_date -= timedelta(days=1)
    return operational_date - timedelta(days=operational_date.weekday())


def normalize_planning(payloads: Iterable[dict[str, Any]], tz: ZoneInfo) -> list[PlanningInterval]:
    """Normalize one or more personPlanning responses to absolute intervals."""
    intervals: list[PlanningInterval] = []

    for payload in payloads:
        planning = payload.get("planning") or {}
        fire_unit = planning.get("fireUnit") or {}
        planning_start = parse_clock(fire_unit.get("planningStartTime"))

        for staff_day in planning.get("staff") or []:
            day_str = staff_day.get("planningStartDate")
            if not day_str:
                continue
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue

            base = datetime.combine(day, planning_start, tzinfo=tz)
            for period in staff_day.get("planningPeriods") or []:
                state = period.get("state") or {}
                code = str(state.get("code") or "").strip()
                name = str(state.get("name") or code or "INCONNU").strip()
                if not code and not name:
                    continue

                start_raw = period.get("startTime")
                end_raw = period.get("endTime")
                start = base + parse_hms(start_raw)
                end = base + parse_hms(end_raw)

                # ARTEMIS represents the last second of an operational day as
                # 23:59:59. Treat it as the next boundary to avoid a 1-second gap.
                if end_raw == "23:59:59":
                    end += timedelta(seconds=1)

                if end <= start:
                    continue

                intervals.append(
                    PlanningInterval(
                        start=start,
                        end=end,
                        status=StatusValue(code=code, name=name),
                    )
                )

    intervals.sort(key=lambda item: (item.start, item.end))
    return intervals


def build_planning_snapshot(
    intervals: list[PlanningInterval],
    now: datetime,
    *,
    staff_name: str,
    staff_id: str,
    unit_id: str,
    lookahead_weeks: int,
) -> PlanningSnapshot:
    """Build current status and next real status change."""
    current_index: int | None = None
    for index, interval in enumerate(intervals):
        if interval.start <= now < interval.end:
            current_index = index
            break

    if current_index is None:
        return PlanningSnapshot(
            current=None,
            current_since=None,
            current_period_end=None,
            next_status=None,
            next_change=None,
            staff_name=staff_name,
            staff_id=staff_id,
            unit_id=unit_id,
            lookahead_weeks=lookahead_weeks,
        )

    current_interval = intervals[current_index]
    current = current_interval.status

    # Walk backwards to find the start of the contiguous same-status block.
    current_since = current_interval.start
    cursor = current_index - 1
    while cursor >= 0:
        previous = intervals[cursor]
        if previous.status.code != current.code:
            break
        # Allow the 1-second edge conventions used by ARTEMIS.
        if previous.end < current_since - timedelta(seconds=1):
            break
        current_since = previous.start
        cursor -= 1

    # Ignore ARTEMIS row/day boundaries when the actual status is unchanged.
    next_status: StatusValue | None = None
    next_change: datetime | None = None
    for interval in intervals[current_index + 1 :]:
        if interval.start < now:
            continue
        if interval.status.code == current.code:
            continue
        next_status = interval.status
        next_change = interval.start
        break

    # ARTEMIS/WebEvo commonly splits a continuous status at row/day boundaries.
    # Extend the current-period end through adjacent same-status intervals so a
    # manual override can remain useful even when there is no future *different*
    # status in the look-ahead window.
    current_period_end = current_interval.end
    for interval in intervals[current_index + 1 :]:
        if interval.status.code != current.code:
            break
        if interval.start > current_period_end + timedelta(seconds=1):
            break
        if interval.end > current_period_end:
            current_period_end = interval.end

    return PlanningSnapshot(
        current=current,
        current_since=current_since,
        current_period_end=current_period_end,
        next_status=next_status,
        next_change=next_change,
        staff_name=staff_name,
        staff_id=staff_id,
        unit_id=unit_id,
        lookahead_weeks=lookahead_weeks,
    )



def cycle_status_code(current_code: str) -> str | None:
    """Return the next state in the safe personal availability cycle."""
    try:
        index = STATUS_CYCLE.index(current_code)
    except ValueError:
        return None
    return STATUS_CYCLE[(index + 1) % len(STATUS_CYCLE)]


def _save_period(
    code: str,
    start: datetime,
    end: datetime,
    *,
    operational_end: datetime,
) -> dict[str, Any]:
    """Convert an absolute segment to the fields sent by WebEvo's editor."""
    # The WebEvo editor represents the end of the operational day as the last
    # minute before the boundary (for a 07:00 day start this is 06:59).
    end_for_wire = end
    if end >= operational_end:
        end_for_wire = operational_end - timedelta(minutes=1)

    return {
        "state": {"code": code},
        "realStartTimeHour": str(start.hour),
        "realStartTimeMinute": str(start.minute),
        "realEndTimeHour": str(end_for_wire.hour),
        "realEndTimeMinute": str(end_for_wire.minute),
    }


def build_status_override_requests(
    payloads: Iterable[dict[str, Any]],
    *,
    staff_id: str,
    start: datetime,
    end: datetime,
    new_status_code: str,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    """Build saveStaff requests changing only [start, end) for one person.

    Existing planning boundaries outside the override window are preserved.
    The function may return several requests because WebEvo stores one row per
    operational day.
    """
    if end <= start:
        return []

    requests: list[tuple[datetime, dict[str, Any]]] = []
    seen_rows: set[tuple[str, str]] = set()

    for payload in payloads:
        planning = payload.get("planning") or {}
        fire_unit = planning.get("fireUnit") or {}
        planning_start = parse_clock(fire_unit.get("planningStartTime"))

        for staff_day in planning.get("staff") or []:
            if str(staff_day.get("id") or "") != staff_id:
                continue

            day_str = str(staff_day.get("planningStartDate") or "")
            if not day_str:
                continue
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue

            operational_start = datetime.combine(day, planning_start, tzinfo=tz)
            operational_end = operational_start + timedelta(days=1)
            if operational_end <= start or operational_start >= end:
                continue

            planning_id = staff_day.get("planningId")
            if planning_id in (None, ""):
                raise ValueError(
                    f"ARTEMIS did not expose a planning id for {day_str}"
                )

            row_key = (day_str, str(planning_id))
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)

            periods_out: list[dict[str, Any]] = []
            for period in staff_day.get("planningPeriods") or []:
                state = period.get("state") or {}
                original_code = str(state.get("code") or "").strip()
                if not original_code:
                    continue

                raw_start = period.get("startTime")
                raw_end = period.get("endTime")
                period_start = operational_start + parse_hms(raw_start)
                period_end = operational_start + parse_hms(raw_end)
                if raw_end == "23:59:59":
                    period_end = operational_end
                if period_end <= period_start:
                    continue

                overlap_start = max(period_start, start)
                overlap_end = min(period_end, end)

                if overlap_end <= overlap_start:
                    periods_out.append(
                        _save_period(
                            original_code,
                            period_start,
                            period_end,
                            operational_end=operational_end,
                        )
                    )
                    continue

                if period_start < overlap_start:
                    periods_out.append(
                        _save_period(
                            original_code,
                            period_start,
                            overlap_start,
                            operational_end=operational_end,
                        )
                    )

                periods_out.append(
                    _save_period(
                        new_status_code,
                        overlap_start,
                        overlap_end,
                        operational_end=operational_end,
                    )
                )

                if overlap_end < period_end:
                    periods_out.append(
                        _save_period(
                            original_code,
                            overlap_end,
                            period_end,
                            operational_end=operational_end,
                        )
                    )

            if not periods_out:
                continue

            request = {
                "staffMember": {
                    "id": staff_id,
                    "planningId": planning_id,
                    "priority": str(staff_day.get("priority", 10)),
                    "planningPeriods": periods_out,
                    "name": str(staff_day.get("name") or ""),
                    "firstName": str(staff_day.get("firstName") or ""),
                },
                "forceUbiquity": False,
            }
            requests.append((operational_start, request))

    requests.sort(key=lambda item: item[0])
    return [request for _day, request in requests]

def format_address(address: dict[str, Any] | None, *, with_city: bool = True) -> str:
    """Match the address composition used by ARTEMIS WebEvo."""
    if not address:
        return ""

    chunks: list[str] = []
    if address.get("type") and address.get("type") != "IT":
        chunks.append("CRM")
    if with_city and address.get("city"):
        chunks.append(str(address["city"]))
    for key in ("line1", "line2", "etareName", "knownPoint"):
        if address.get(key):
            chunks.append(str(address[key]))

    street_bits: list[str] = []
    for key in (
        "streetNumber",
        "cplStreetName",
        "landMark",
        "streetCategory",
        "streetPrefix",
        "streetName",
    ):
        if address.get(key):
            street_bits.append(str(address[key]))
    if street_bits:
        chunks.append(" ".join(street_bits))

    for key in ("sectionName", "crossing", "ward"):
        if address.get(key):
            chunks.append(str(address[key]))

    if address.get("cp"):
        chunks.append(str(address["cp"]))

    return " - ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())


def operation_addresses(operation: dict[str, Any]) -> list[str]:
    """Return unique formatted addresses for an operation."""
    addresses: list[str] = []
    raw_addresses = operation.get("addresses") or []
    if isinstance(raw_addresses, list):
        for item in raw_addresses:
            if isinstance(item, dict):
                formatted = format_address(item)
                if formatted and formatted not in addresses:
                    addresses.append(formatted)

    if not addresses and isinstance(operation.get("address"), dict):
        formatted = format_address(operation["address"])
        if formatted:
            addresses.append(formatted)

    return addresses



def _coerce_coordinate(value: Any, *, latitude: bool) -> float | None:
    """Return a valid WGS84 coordinate or None.

    ARTEMIS deployments may expose coordinates using slightly different field
    names. Only values that already look like WGS84 latitude/longitude are
    accepted; projected X/Y coordinates are deliberately ignored.
    """
    if value in (None, ""):
        return None
    try:
        numeric = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if latitude and -90 <= numeric <= 90:
        return numeric
    if not latitude and -180 <= numeric <= 180:
        return numeric
    return None


def operation_coordinates(operation: dict[str, Any]) -> tuple[float | None, float | None, str]:
    """Extract the first WGS84 latitude/longitude pair exposed by ARTEMIS.

    The live-operation schema is not identical across all WebEvo deployments,
    so this supports common field names while avoiding ambiguous projected
    ``x``/``y`` values. The returned source is informational only.
    """
    latitude_keys = (
        "latitude",
        "lat",
        "gpsLatitude",
        "latitudeGps",
        "latitudeWgs84",
        "latWgs84",
    )
    longitude_keys = (
        "longitude",
        "lon",
        "lng",
        "gpsLongitude",
        "longitudeGps",
        "longitudeWgs84",
        "lonWgs84",
        "lngWgs84",
    )

    def pair_from_dict(value: dict[str, Any]) -> tuple[float | None, float | None]:
        lower = {str(key).lower(): item for key, item in value.items()}
        lat = next(
            (
                _coerce_coordinate(lower.get(key.lower()), latitude=True)
                for key in latitude_keys
                if key.lower() in lower
            ),
            None,
        )
        lon = next(
            (
                _coerce_coordinate(lower.get(key.lower()), latitude=False)
                for key in longitude_keys
                if key.lower() in lower
            ),
            None,
        )
        if lat is not None and lon is not None:
            return lat, lon

        # GeoJSON Point: coordinates are [longitude, latitude].
        coordinates = value.get("coordinates")
        point_type = str(value.get("type") or "").lower()
        if point_type == "point" and isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            lon = _coerce_coordinate(coordinates[0], latitude=False)
            lat = _coerce_coordinate(coordinates[1], latitude=True)
            if lat is not None and lon is not None:
                return lat, lon

        return None, None

    candidates: list[tuple[str, Any]] = [("operation", operation)]
    if isinstance(operation.get("address"), dict):
        candidates.append(("address", operation["address"]))
    raw_addresses = operation.get("addresses") or []
    if isinstance(raw_addresses, list):
        candidates.extend(
            (f"addresses[{index}]", item)
            for index, item in enumerate(raw_addresses)
            if isinstance(item, dict)
        )

    # Some deployments wrap coordinates in one of these objects.
    for container_name in ("location", "position", "gps", "geometry", "coordinate"):
        value = operation.get(container_name)
        if isinstance(value, dict):
            candidates.append((container_name, value))
        address = operation.get("address")
        if isinstance(address, dict) and isinstance(address.get(container_name), dict):
            candidates.append((f"address.{container_name}", address[container_name]))

    for source, candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        lat, lon = pair_from_dict(candidate)
        if lat is not None and lon is not None:
            return lat, lon, source

    return None, None, ""


def operation_navigation_uri(operation: dict[str, Any], address: str = "") -> str:
    """Return an Android-friendly map URI for the operation location."""
    from urllib.parse import quote_plus

    latitude, longitude, _source = operation_coordinates(operation)
    if latitude is not None and longitude is not None:
        return f"geo:{latitude:.6f},{longitude:.6f}?q={latitude:.6f},{longitude:.6f}"
    if address and address != "Adresse non communiquee":
        return f"geo:0,0?q={quote_plus(address)}"
    return ""

def _state_parts(item: dict[str, Any]) -> tuple[str, str]:
    """Return state code and name from a WebEvo object."""
    state = item.get("state") or {}
    if not isinstance(state, dict):
        value = str(state or "").strip()
        return value, value
    return (
        str(state.get("code") or "").strip(),
        str(state.get("name") or "").strip(),
    )


def _unit_label(unit: Any) -> str:
    """Return the best centre/unit label exposed by ARTEMIS."""
    if isinstance(unit, dict):
        for key in ("shortname", "shortName", "code", "name", "id"):
            value = unit.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""
    if unit not in (None, ""):
        return str(unit).strip()
    return ""


def operation_identifier(operation: dict[str, Any]) -> str:
    """Return the most stable identifier available for an operation."""
    for key in ("id", "codeOperation", "number", "num"):
        value = operation.get(key)
        if value not in (None, ""):
            return str(value)
    return "unknown"


def operation_snapshot_data(operation: dict[str, Any]) -> dict[str, Any]:
    """Normalize one active operation for the interventions sensor.

    The sensor intentionally exposes structured data only. Notification wording
    and lifecycle handling belong in Home Assistant automations, not in the
    integration.
    """
    operation_id = operation_identifier(operation)
    number = str(
        operation.get("number")
        or operation.get("num")
        or operation.get("codeOperation")
        or operation_id
    )
    title = str(operation.get("disasterLabel") or "Intervention").strip()
    addresses = operation_addresses(operation)
    address = addresses[0] if addresses else "Adresse non communiquee"
    latitude, longitude, _source = operation_coordinates(operation)
    state_code, state_name = _state_parts(operation)

    # Build a lookup so vehicle owner ids can be rendered as the human-readable
    # centre short name exposed in the same operation payload.
    unit_labels: dict[str, str] = {}
    for unit in operation.get("fireUnits") or []:
        if not isinstance(unit, dict):
            continue
        label = _unit_label(unit)
        for key in ("id", "code", "name", "shortname", "shortName"):
            value = unit.get(key)
            if value not in (None, "") and label:
                unit_labels[str(value)] = label

    vehicles: list[dict[str, str]] = []
    for vehicle in operation.get("vehicles") or []:
        if not isinstance(vehicle, dict):
            continue

        owner = vehicle.get("ownerFireUnit")
        center = _unit_label(owner)
        if center in unit_labels:
            center = unit_labels[center]
        elif isinstance(owner, dict):
            for key in ("id", "code", "name", "shortname", "shortName"):
                value = owner.get(key)
                if value not in (None, "") and str(value) in unit_labels:
                    center = unit_labels[str(value)]
                    break

        vehicle_state_code, vehicle_state_name = _state_parts(vehicle)
        vehicles.append(
            {
                "center": center,
                "name": str(
                    vehicle.get("name")
                    or vehicle.get("ack")
                    or vehicle.get("id")
                    or "Engin"
                ).strip(),
                "state_code": vehicle_state_code,
                "state_name": vehicle_state_name,
            }
        )

    vehicles.sort(key=lambda item: (item["center"], item["name"]))

    created = (
        operation.get("dateStartRdv")
        if operation.get("typeCode") == "D1"
        else operation.get("dateCreation")
    )

    return {
        "id": operation_id,
        "number": number,
        "title": title,
        "created": created,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "navigation_uri": operation_navigation_uri(operation, address),
        "state_code": state_code,
        "state_name": state_name,
        "vehicles": vehicles,
    }
