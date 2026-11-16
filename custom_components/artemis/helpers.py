"""Pure helpers shared by ARTEMIS backends."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from urllib.parse import quote_plus, unquote_plus
from zoneinfo import ZoneInfo

from .const import STATUS_CYCLE
from .models import PlanningInterval, PlanningSnapshot, StatusValue

_LATITUDE_KEYS = (
    "latitude",
    "lat",
    "gpsLatitude",
    "latitudeGps",
    "latitudeWgs84",
    "latWgs84",
)
_LONGITUDE_KEYS = (
    "longitude",
    "lon",
    "lng",
    "gpsLongitude",
    "longitudeGps",
    "longitudeWgs84",
    "lonWgs84",
    "lngWgs84",
)
_UNIT_LABEL_KEYS = ("shortname", "shortName", "code", "name", "id")


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


def next_shift_boundary(
    moment: datetime,
    planning_start: time = time(7, 0),
) -> datetime:
    """Return the next 12-hour duty handover derived from planningStartTime.

    With the SDIS 39 07:00 operational-day start, the handovers are 07:00 and
    19:00. Using the server-provided planning start keeps the behavior portable.
    """
    local_clock = moment.timetz().replace(tzinfo=None)
    operational_date = moment.date()
    if local_clock < planning_start:
        operational_date -= timedelta(days=1)

    operational_start = datetime.combine(
        operational_date,
        planning_start,
        tzinfo=moment.tzinfo,
    )
    midpoint = operational_start + timedelta(hours=12)
    operational_end = operational_start + timedelta(days=1)

    return midpoint if moment < midpoint else operational_end


def status_override_boundary(
    moment: datetime,
    planning_start: time,
    next_change: datetime | None,
    preserved_boundary: datetime | None = None,
) -> datetime:
    """Return the safe end for a manual status override.

    The boundary can never extend beyond the next duty handover. A real planned
    status change or an already-preserved earlier boundary wins when it occurs
    sooner. This also clamps stale boundaries created by older integration
    versions.
    """
    candidates = [next_shift_boundary(moment, planning_start)]
    if next_change is not None and next_change > moment:
        candidates.append(next_change)
    if preserved_boundary is not None and preserved_boundary > moment:
        candidates.append(preserved_boundary)
    return min(candidates)


def next_staff_row_status_change(
    staff_day: dict[str, Any],
    planning_start: time,
    moment: datetime,
    tz: ZoneInfo,
) -> tuple[datetime | None, StatusValue | None]:
    """Return the next different status from one exact WebEvo staff row.

    This intentionally inspects the fresh row that will be saved instead of the
    multi-week snapshot used for display. The write path can therefore never
    skip an already-programmed change present in the row being edited.
    """
    day_str = str(staff_day.get("planningStartDate") or "")
    if not day_str:
        return None, None
    try:
        day = date.fromisoformat(day_str)
    except ValueError:
        return None, None

    base = datetime.combine(day, planning_start, tzinfo=tz)
    intervals: list[PlanningInterval] = []
    for period in staff_day.get("planningPeriods") or []:
        state = period.get("state") or {}
        code = str(state.get("code") or "").strip()
        if not code:
            continue
        name = str(state.get("name") or code).strip()
        raw_start = period.get("startTime")
        raw_end = period.get("endTime")
        start = base + parse_hms(raw_start)
        end = base + parse_hms(raw_end)
        if raw_end == "23:59:59":
            end = base + timedelta(days=1)
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
    current_index: int | None = None
    for index, interval in enumerate(intervals):
        if interval.start <= moment < interval.end:
            current_index = index
            break
    if current_index is None:
        return None, None

    current_code = intervals[current_index].status.code
    for interval in intervals[current_index + 1 :]:
        if interval.start <= moment:
            continue
        if interval.status.code != current_code:
            return interval.start, interval.status

    return None, None


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


def build_status_override_request(
    staff_day: dict[str, Any],
    *,
    staff_id: str,
    planning_start: time,
    start: datetime,
    end: datetime,
    new_status_code: str,
    tz: ZoneInfo,
) -> dict[str, Any]:
    """Build one safe saveStaff request from one freshly read WebEvo row."""

    if end <= start:
        raise ValueError("ARTEMIS status override has an invalid time range")
    if str(staff_day.get("id") or "") != staff_id:
        raise ValueError("ARTEMIS returned a planning row for another staff member")

    day_str = str(staff_day.get("planningStartDate") or "")
    try:
        day = date.fromisoformat(day_str)
    except ValueError as err:
        raise ValueError("ARTEMIS returned an invalid planning date") from err

    operational_start = datetime.combine(day, planning_start, tzinfo=tz)
    operational_end = operational_start + timedelta(days=1)
    if start < operational_start or end > operational_end:
        raise ValueError("ARTEMIS status override must stay within one planning row")

    planning_id = staff_day.get("planningId")
    if planning_id in (None, ""):
        raise ValueError("ARTEMIS did not expose the current planning id")

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
        raise ValueError("ARTEMIS current planning row contains no writable periods")

    return {
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


def format_address(address: dict[str, Any] | None) -> str:
    """Match the address composition used by ARTEMIS WebEvo."""
    if not address:
        return ""

    chunks: list[str] = []
    if address.get("type") and address.get("type") != "IT":
        chunks.append("CRM")
    if address.get("city"):
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
    seen: set[str] = set()
    raw_addresses = operation.get("addresses") or []
    if isinstance(raw_addresses, list):
        for item in raw_addresses:
            if not isinstance(item, dict):
                continue
            formatted = format_address(item)
            if formatted and formatted not in seen:
                seen.add(formatted)
                addresses.append(formatted)

    if not addresses and isinstance(operation.get("address"), dict):
        formatted = format_address(operation["address"])
        if formatted:
            addresses.append(formatted)

    return addresses


def coerce_coordinate(value: Any, *, latitude: bool) -> float | None:
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


_URL_COORDINATE_RE = re.compile(
    r"(?<![0-9.])(-?[0-9]{1,2}(?:\.[0-9]+)?),\s*(-?[0-9]{1,3}(?:\.[0-9]+)?)(?![0-9.])"
)


def navigation_url_coordinates(value: Any) -> tuple[float | None, float | None]:
    """Extract WGS84 coordinates embedded in a navigation URL without geocoding."""

    if not isinstance(value, str) or not value.strip():
        return None, None

    decoded = unquote_plus(value.strip())
    for match in _URL_COORDINATE_RE.finditer(decoded):
        latitude = coerce_coordinate(match.group(1), latitude=True)
        longitude = coerce_coordinate(match.group(2), latitude=False)
        if (
            latitude is not None
            and longitude is not None
            and not (latitude == 0 and longitude == 0)
        ):
            return latitude, longitude

    return None, None


def normalize_geocoding_query(address: Any) -> str:
    """Normalize ARTEMIS display addresses for the French address geocoder."""

    value = " ".join(str(address or "").split()).strip(" -")
    if not value:
        return ""

    if " - " in value:
        city, street = (part.strip() for part in value.split(" - ", 1))
        if city and street and any(char.isdigit() for char in street):
            return f"{street} {city}"

    return value


def parse_geocoding_result(
    payload: Any,
    *,
    minimum_score: float = 0.6,
) -> tuple[float | None, float | None, float | None, str]:
    """Return the first sufficiently confident GeoJSON address result."""

    if not isinstance(payload, dict):
        return None, None, None, ""

    features = payload.get("features")
    if not isinstance(features, list):
        return None, None, None, ""

    for feature in features:
        if not isinstance(feature, dict):
            continue

        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue

        try:
            score = float(properties.get("score"))
        except (TypeError, ValueError):
            continue
        if score < minimum_score:
            continue

        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
            continue

        longitude = coerce_coordinate(coordinates[0], latitude=False)
        latitude = coerce_coordinate(coordinates[1], latitude=True)
        if latitude is None or longitude is None:
            continue

        return (
            latitude,
            longitude,
            score,
            str(properties.get("label") or "").strip(),
        )

    return None, None, None, ""


def operation_coordinates(
    operation: dict[str, Any],
) -> tuple[float | None, float | None]:
    """Extract the first plausible WGS84 latitude/longitude pair."""

    def pair_from_dict(value: dict[str, Any]) -> tuple[float | None, float | None]:
        lower = {str(key).lower(): item for key, item in value.items()}
        latitude = next(
            (
                coerce_coordinate(lower.get(key.lower()), latitude=True)
                for key in _LATITUDE_KEYS
                if key.lower() in lower
            ),
            None,
        )
        longitude = next(
            (
                coerce_coordinate(lower.get(key.lower()), latitude=False)
                for key in _LONGITUDE_KEYS
                if key.lower() in lower
            ),
            None,
        )
        if latitude is not None and longitude is not None:
            return latitude, longitude

        coordinates = value.get("coordinates")
        if (
            str(value.get("type") or "").lower() == "point"
            and isinstance(coordinates, (list, tuple))
            and len(coordinates) >= 2
        ):
            longitude = coerce_coordinate(coordinates[0], latitude=False)
            latitude = coerce_coordinate(coordinates[1], latitude=True)
            if latitude is not None and longitude is not None:
                return latitude, longitude

        return None, None

    candidates: list[dict[str, Any]] = [operation]
    address = operation.get("address")
    if isinstance(address, dict):
        candidates.append(address)

    raw_addresses = operation.get("addresses") or []
    if isinstance(raw_addresses, (list, tuple)):
        candidates.extend(item for item in raw_addresses if isinstance(item, dict))

    for container_name in ("location", "position", "gps", "geometry", "coordinate"):
        value = operation.get(container_name)
        if isinstance(value, dict):
            candidates.append(value)
        if isinstance(address, dict):
            nested = address.get(container_name)
            if isinstance(nested, dict):
                candidates.append(nested)

    for candidate in candidates:
        latitude, longitude = pair_from_dict(candidate)
        if latitude is not None and longitude is not None:
            return latitude, longitude

    for key in ("url", "navigation_uri", "navigationUrl", "mapUrl", "map_url"):
        latitude, longitude = navigation_url_coordinates(operation.get(key))
        if latitude is not None and longitude is not None:
            return latitude, longitude

    return None, None


def navigation_uri(
    latitude: float | None,
    longitude: float | None,
    address: str = "",
) -> str:
    """Return an app-agnostic Android map deep link."""
    if latitude is not None and longitude is not None:
        coordinate = f"{latitude:.6f},{longitude:.6f}"
        return f"deep-link://geo:0,0?q={quote_plus(coordinate)}"
    if address and address != "Adresse non communiquee":
        return f"deep-link://geo:0,0?q={quote_plus(address)}"
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
        for key in _UNIT_LABEL_KEYS:
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
    latitude, longitude = operation_coordinates(operation)
    state_code, state_name = _state_parts(operation)

    # Build a lookup so vehicle owner ids can be rendered as the human-readable
    # centre short name exposed in the same operation payload.
    unit_labels: dict[str, str] = {}
    for unit in operation.get("fireUnits") or []:
        if not isinstance(unit, dict):
            continue
        label = _unit_label(unit)
        for key in _UNIT_LABEL_KEYS:
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
            for key in _UNIT_LABEL_KEYS:
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

    centers: list[dict[str, str]] = []
    for unit in operation.get("fireUnits") or []:
        if not isinstance(unit, dict):
            continue
        unit_state_code, unit_state_name = _state_parts(unit)
        centers.append(
            {
                "id": str(unit.get("id") or ""),
                "name": str(unit.get("name") or "").strip(),
                "short_name": str(
                    unit.get("shortname") or unit.get("shortName") or ""
                ).strip(),
                "state_code": unit_state_code,
                "state_name": unit_state_name,
            }
        )
    centers.sort(key=lambda item: (item["short_name"], item["name"], item["id"]))

    communication_group = str(
        operation.get("grpRadio")
        or operation.get("communicationGroup")
        or operation.get("radioGroup")
        or ""
    ).strip()
    station_name = str(operation.get("stationName") or "").strip()
    station_receiver_name = str(operation.get("stationReceiverName") or "").strip()

    external_services: list[dict[str, Any]] = []
    for service in operation.get("externalServices") or []:
        if not isinstance(service, dict):
            continue
        service_state_code, service_state_name = _state_parts(service)
        external_services.append(
            {
                "id": str(service.get("id") or service.get("code") or ""),
                "name": str(service.get("name") or "").strip(),
                "state_code": service_state_code,
                "state_name": service_state_name,
            }
        )
    external_services.sort(key=lambda item: (item["name"], item["id"]))

    created = (
        operation.get("dateStartRdv")
        if operation.get("typeCode") == "D1"
        else operation.get("dateCreation")
    )

    person_count = operation.get("personCount")
    if person_count is None:
        person_count = operation.get("nbPers")

    return {
        "id": operation_id,
        "number": number,
        "title": title,
        "created": created,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "navigation_uri": navigation_uri(latitude, longitude, address),
        "state_code": state_code,
        "state_name": state_name,
        "type_code": operation.get("typeCode"),
        "resource_count": len(vehicles),
        "person_count": person_count,
        "communication_group": communication_group,
        "station_name": station_name,
        "station_receiver_name": station_receiver_name,
        "centers": centers,
        "vehicles": vehicles,
        "external_services": external_services,
        "history": [],
    }


_ARCHIVE_META_KEYS = {"active", "first_seen", "last_seen", "ended"}

_INTERVENTION_ICON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("FEU", "INCENDIE", "FUMEE", "BRULE", "EXPLOSION"), "mdi:fire"),
    (
        (
            "ACCIDENT",
            "AVP",
            "COLLISION",
            "SORTIE DE ROUTE",
            "DESINCARCERATION",
            "VEHICULE",
        ),
        "mdi:car-emergency",
    ),
    (("ASCENSEUR",), "mdi:elevator-passenger"),
    (("CHUTE", "PERSONNE COINCEE", "PERSONNE CAPTIVE"), "mdi:account-alert"),
    (
        (
            "SECOURS A PERSONNE",
            "MALAISE",
            "DETRESSE",
            "ARRET CARDIAQUE",
            "INCONSCIENT",
        ),
        "mdi:ambulance",
    ),
    (("NOYADE", "AQUATIQUE", "NAUTIQUE", "INONDATION"), "mdi:waves"),
    (("GAZ", "MONOXYDE"), "mdi:gas-cylinder"),
    (("ANIMAL", "CHIEN", "CHAT"), "mdi:paw"),
    (("EFFONDREMENT", "OUVERTURE DE PORTE"), "mdi:home-alert"),
    (("ARBRE", "TEMPETE"), "mdi:tree"),
)


def _normalized_intervention_label(value: Any) -> str:
    """Return an uppercase accent-insensitive label for icon matching."""

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char))
        .upper()
        .split()
    )


def intervention_icon(title: Any, type_code: Any = "") -> str:
    """Return a visual MDI icon for an intervention title/type."""

    label = _normalized_intervention_label(f"{title or ''} {type_code or ''}")
    for keywords, icon in _INTERVENTION_ICON_RULES:
        if any(keyword in label for keyword in keywords):
            return icon
    return "mdi:alert"


def intervention_reference_time(
    record: dict[str, Any],
    *,
    default_tz: tzinfo = UTC,
) -> datetime | None:
    """Return the intervention creation time, falling back to first observation."""

    for key in ("created", "first_seen"):
        raw = record.get(key)
        if raw in (None, ""):
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=default_tz)
        return parsed.astimezone(UTC)
    return None


def intervention_is_recent(
    record: dict[str, Any],
    now: datetime,
    max_age: timedelta,
    *,
    default_tz: tzinfo = UTC,
) -> bool:
    """Return whether an archived intervention belongs in the recent map feed."""

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    reference = intervention_reference_time(record, default_tz=default_tz)
    if reference is None:
        return True
    return reference >= now.astimezone(UTC) - max_age


def intervention_archive_key(operation: dict[str, Any]) -> str:
    """Return a stable archive key without assuming operation ids are eternal."""

    operation_id = str(
        operation.get("id") or operation.get("number") or "unknown"
    ).strip()
    created = str(operation.get("created") or "").strip()
    return f"{operation_id}|{created}" if created else operation_id


def merge_intervention_archive(
    records: dict[str, dict[str, Any]],
    operations: Iterable[dict[str, Any]],
    observed_at: str,
) -> tuple[set[str], set[str]]:
    """Merge an active snapshot into a persistent intervention archive.

    Returns (changed_keys, new_keys). Metadata changes only when the normalized
    intervention payload changes or when an intervention becomes inactive, so
    routine polling does not create needless recorder/storage churn.
    """

    changed: set[str] = set()
    new: set[str] = set()
    active_keys: set[str] = set()

    for operation in operations:
        key = intervention_archive_key(operation)
        active_keys.add(key)
        previous = records.get(key)
        first_seen = (
            str(previous.get("first_seen"))
            if previous and previous.get("first_seen")
            else observed_at
        )
        previous_payload = (
            {
                name: value
                for name, value in previous.items()
                if name not in _ARCHIVE_META_KEYS
            }
            if previous
            else None
        )
        payload_changed = previous_payload != operation
        became_active = previous is not None and not bool(previous.get("active"))

        if previous is None or payload_changed or became_active:
            records[key] = {
                **operation,
                "active": True,
                "first_seen": first_seen,
                "last_seen": observed_at,
                "ended": None,
            }
            changed.add(key)
            if previous is None:
                new.add(key)

    for key, previous in tuple(records.items()):
        if key in active_keys or not bool(previous.get("active")):
            continue
        records[key] = {
            **previous,
            "active": False,
            "ended": observed_at,
        }
        changed.add(key)

    return changed, new

