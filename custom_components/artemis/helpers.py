"""Pure helpers for ARTEMIS WebEvo data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape
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

    return PlanningSnapshot(
        current=current,
        current_since=current_since,
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


def _state_parts(item: dict[str, Any]) -> tuple[str, str, str]:
    """Return state code, name and a compact display label."""
    state = item.get("state") or {}
    if not isinstance(state, dict):
        return "", "", str(state or "").strip()
    code = str(state.get("code") or "").strip()
    name = str(state.get("name") or "").strip()
    if code and name and code != name:
        label = f"{code} \u2013 {name}"
    else:
        label = name or code
    return code, name, label


def _state_label(item: dict[str, Any]) -> str:
    """Return a compact state label for backward-compatible string fields."""
    return _state_parts(item)[2]


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


def operation_event_data(
    operation: dict[str, Any],
    *,
    lifecycle: str = "updated",
    active: bool = True,
) -> dict[str, Any]:
    """Build structured operation data plus backward-compatible text fields."""
    operation_id = operation_identifier(operation)
    number = str(
        operation.get("number")
        or operation.get("num")
        or operation.get("codeOperation")
        or operation_id
    )
    disaster = str(operation.get("disasterLabel") or "Intervention").strip()
    addresses = operation_addresses(operation)
    address = addresses[0] if addresses else "Adresse non communiquee"
    created = (
        operation.get("dateStartRdv")
        if operation.get("typeCode") == "D1"
        else operation.get("dateCreation")
    )
    state_code, state_name, op_state = _state_parts(operation)

    fire_units: list[str] = []
    fire_units_data: list[dict[str, str]] = []
    unit_labels: dict[str, str] = {}
    for unit in operation.get("fireUnits") or []:
        if not isinstance(unit, dict):
            continue
        label = _unit_label(unit) or "Centre"
        unit_state_code, unit_state_name, unit_state = _state_parts(unit)
        display = f"{label} ({unit_state})" if unit_state else label
        fire_units.append(display)
        fire_units_data.append(
            {
                "id": str(unit.get("id") or ""),
                "code": str(unit.get("code") or ""),
                "name": str(unit.get("name") or ""),
                "shortname": str(unit.get("shortname") or unit.get("shortName") or ""),
                "label": label,
                "state": unit_state,
                "state_code": unit_state_code,
                "state_name": unit_state_name,
            }
        )
        for key in ("id", "code", "name", "shortname", "shortName"):
            value = unit.get(key)
            if value not in (None, ""):
                unit_labels[str(value)] = label

    vehicles: list[str] = []
    vehicles_data: list[dict[str, str]] = []
    for vehicle in operation.get("vehicles") or []:
        if not isinstance(vehicle, dict):
            continue

        vehicle_name = str(vehicle.get("name") or vehicle.get("ack") or vehicle.get("id") or "Engin").strip()
        ack = str(vehicle.get("ack") or "").strip()
        vehicle_type = vehicle.get("type") or {}
        type_name = (
            str(vehicle_type.get("name") or "").strip()
            if isinstance(vehicle_type, dict)
            else str(vehicle_type or "").strip()
        )
        vehicle_state_code, vehicle_state_name, vehicle_state = _state_parts(vehicle)
        eta = str(vehicle.get("estimatedTime") or "").strip()
        gfo = " ".join(
            str(vehicle.get(key) or "").strip()
            for key in ("gfoCode", "gfoLevel")
            if vehicle.get(key)
        )

        owner = vehicle.get("ownerFireUnit")
        center = _unit_label(owner)
        if center and center in unit_labels:
            center = unit_labels[center]
        elif isinstance(owner, dict):
            for key in ("id", "code", "name", "shortname", "shortName"):
                value = owner.get(key)
                if value not in (None, "") and str(value) in unit_labels:
                    center = unit_labels[str(value)]
                    break

        display_name = vehicle_name
        if ack and ack not in vehicle_name:
            display_name = f"{ack} {vehicle_name}"
        extras = [
            part
            for part in (
                type_name,
                vehicle_state,
                f"ETA {eta}" if eta else "",
                gfo,
            )
            if part
        ]
        display = display_name
        if extras:
            display = f"{display} - " + " | ".join(extras)
        vehicles.append(display)
        vehicles_data.append(
            {
                "id": str(vehicle.get("id") or ""),
                "center": center,
                "name": vehicle_name,
                "ack": ack,
                "type": type_name,
                "state": vehicle_state,
                "state_code": vehicle_state_code,
                "state_name": vehicle_state_name,
                "estimated_time": eta,
                "gfo": gfo,
            }
        )

    external_services: list[str] = []
    external_services_data: list[dict[str, str]] = []
    for service in operation.get("externalServices") or []:
        if not isinstance(service, dict):
            continue
        label = str(service.get("name") or service.get("id") or "Service")
        service_state_code, service_state_name, service_state = _state_parts(service)
        display = f"{label} ({service_state})" if service_state else label
        external_services.append(display)
        external_services_data.append(
            {
                "id": str(service.get("id") or ""),
                "name": label,
                "state": service_state,
                "state_code": service_state_code,
                "state_name": service_state_name,
            }
        )

    # Backward-compatible prebuilt text. New automations should normally use
    # the structured fields above instead of depending on this formatting.
    lines = [f"\U0001f4cd {address}", f"N\u00b0 {number}" + (f" \u00b7 {created}" if created else "")]
    if op_state:
        lines.append(f"\u00c9tat : {op_state}")
    if len(addresses) > 1:
        lines.append("Autres adresses : " + " ; ".join(addresses[1:]))
    if fire_units:
        lines.extend(("", "Centres", *[f"\u2022 {item}" for item in fire_units]))
    if vehicles:
        lines.extend(("", "Engins", *[f"\u2022 {item}" for item in vehicles]))
    if external_services:
        lines.extend(("", "Services", *[f"\u2022 {item}" for item in external_services]))

    return {
        "id": operation_id,
        "number": number,
        "disaster": disaster,
        "address": address,
        "addresses": addresses,
        "created": created,
        "state": op_state,
        "state_code": state_code,
        "state_name": state_name,
        "active": active,
        "lifecycle": lifecycle,
        "fire_units": fire_units,
        "fire_units_data": fire_units_data,
        "vehicles": vehicles,
        "vehicles_data": vehicles_data,
        "external_services": external_services,
        "external_services_data": external_services_data,
        "title": f"\U0001f692 Nouvelle intervention - {disaster}",
        "message": "\n".join(lines),
        "title_html": f"\U0001f692 Nouvelle intervention - {escape(disaster)}",
    }
