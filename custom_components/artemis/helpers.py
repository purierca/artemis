"""Pure helpers for ARTEMIS WebEvo data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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


def _state_label(item: dict[str, Any]) -> str:
    state = item.get("state") or {}
    code = str(state.get("code") or "").strip()
    name = str(state.get("name") or "").strip()
    if code and name and code != name:
        return f"{code} – {name}"
    return name or code


def operation_identifier(operation: dict[str, Any]) -> str:
    """Return the most stable identifier available for an operation."""
    for key in ("id", "codeOperation", "number", "num"):
        value = operation.get(key)
        if value not in (None, ""):
            return str(value)
    return "unknown"


def operation_event_data(operation: dict[str, Any]) -> dict[str, Any]:
    """Build concise event data and a readable notification message."""
    operation_id = operation_identifier(operation)
    number = str(
        operation.get("number")
        or operation.get("num")
        or operation.get("codeOperation")
        or operation_id
    )
    disaster = str(operation.get("disasterLabel") or "Intervention").strip()
    addresses = operation_addresses(operation)
    address = addresses[0] if addresses else "Adresse non communiquée"
    created = operation.get("dateStartRdv") if operation.get("typeCode") == "D1" else operation.get("dateCreation")
    op_state = _state_label(operation)

    fire_units: list[str] = []
    for unit in operation.get("fireUnits") or []:
        if not isinstance(unit, dict):
            continue
        label = str(unit.get("shortname") or unit.get("name") or unit.get("id") or "Centre")
        state = _state_label(unit)
        if state:
            label = f"{label} ({state})"
        fire_units.append(label)

    vehicles: list[str] = []
    for vehicle in operation.get("vehicles") or []:
        if not isinstance(vehicle, dict):
            continue
        name = " ".join(
            part
            for part in (
                str(vehicle.get("ack") or "").strip(),
                str(vehicle.get("name") or vehicle.get("id") or "Engin").strip(),
            )
            if part
        )
        vehicle_type = vehicle.get("type") or {}
        type_name = str(vehicle_type.get("name") or "").strip() if isinstance(vehicle_type, dict) else ""
        state = _state_label(vehicle)
        eta = str(vehicle.get("estimatedTime") or "").strip()
        gfo = " ".join(
            str(vehicle.get(key) or "").strip()
            for key in ("gfoCode", "gfoLevel")
            if vehicle.get(key)
        )
        extras = [part for part in (type_name, state, f"ETA {eta}" if eta else "", gfo) if part]
        if extras:
            name = f"{name} — " + " · ".join(extras)
        vehicles.append(name)

    external_services: list[str] = []
    for service in operation.get("externalServices") or []:
        if not isinstance(service, dict):
            continue
        label = str(service.get("name") or service.get("id") or "Service")
        state = _state_label(service)
        if state:
            label = f"{label} ({state})"
        external_services.append(label)

    lines = [f"📍 {address}", f"N° {number}" + (f" · {created}" if created else "")]
    if op_state:
        lines.append(f"État : {op_state}")
    if len(addresses) > 1:
        lines.append("Autres adresses : " + " ; ".join(addresses[1:]))
    if fire_units:
        lines.extend(("", "Centres", *[f"• {item}" for item in fire_units]))
    if vehicles:
        lines.extend(("", "Engins", *[f"• {item}" for item in vehicles]))
    if external_services:
        lines.extend(("", "Services", *[f"• {item}" for item in external_services]))

    return {
        "id": operation_id,
        "number": number,
        "disaster": disaster,
        "address": address,
        "addresses": addresses,
        "created": created,
        "state": op_state,
        "fire_units": fire_units,
        "vehicles": vehicles,
        "external_services": external_services,
        "title": f"🚒 Nouvelle intervention — {disaster}",
        "message": "\n".join(lines),
        # Kept separate so a user can use HTML notifications later without
        # changing the integration. The default automation uses plain text.
        "title_html": f"🚒 Nouvelle intervention — {escape(disaster)}",
    }
