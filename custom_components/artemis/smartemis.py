"""Async client for the Smartemis mobile API observed in app version 6.0.22."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiohttp

SMARTEMIS_DIRECTORY_URL = "https://smartemis.artemis-solutions.net/subscription/"
SMARTEMIS_RELEASE_CONTEXT = "2017"

RESULT_OK = 0
BOUNDARY_END_OF_PERIOD = "EOP"

RIGHT_PARAMETERS = "SMART:FCT:PARAM"
YES = "O"
THRIFT_NULL = "null"


class SmartemisError(Exception):
    """Base Smartemis error."""


class SmartemisConnectionError(SmartemisError):
    """Smartemis could not be reached or returned invalid Thrift JSON."""


class SmartemisServiceError(SmartemisError):
    """Smartemis returned a functional/service error."""

    def __init__(self, num: int | None, code: str | None, message: str | None) -> None:
        super().__init__(message or code or f"Smartemis error {num}")
        self.num = num
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SmartemisSdis:
    """One service advertised by the Smartemis central directory."""

    name: str
    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SmartemisSubscriber:
    """An already-registered Smartemis mobile subscriber identity."""

    phone_id: str
    person_id: str
    organization: str


@dataclass(frozen=True, slots=True)
class SmartemisCenter:
    """One centre/person profile exposed to the subscriber."""

    id: str
    name: str
    matricule: str
    person_id: str | None
    last_name: str
    first_name: str
    person_status_code: str | None
    administrative_status_code: str | None
    administrative_status_name: str | None
    rights: tuple[str, ...]

    @property
    def person_name(self) -> str:
        return " ".join(
            value for value in (self.first_name, self.last_name) if value
        ).strip() or self.matricule


@dataclass(frozen=True, slots=True)
class SmartemisUserContext:
    """Smartemis settings needed to select the centre and intervention view."""

    default_center_code: str | None = None
    display_interventions: str | None = None
    display_level: str | None = None


def _scalar(kind: str, value: Any) -> dict[str, Any]:
    return {kind: value}


def _record(fields: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {"rec": {str(field_id): value for field_id, value in fields.items()}}


def _request(method: str, fields: dict[int, dict[str, Any]]) -> str:
    """Build a Thrift JSON CALL envelope for one Smartemis request struct."""

    return json.dumps(
        [1, method, 1, 0, {"1": _record(fields)}],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _unwrap_typed(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        return value
    kind, payload = next(iter(value.items()))
    if kind in {"str", "i8", "i16", "i32", "i64", "dbl"}:
        return payload
    if kind == "tf":
        return bool(payload)
    if kind == "rec":
        return _decode_struct(payload)
    if kind in {"lst", "set"}:
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        element_kind = payload[0]
        count = int(payload[1])
        items = payload[2 : 2 + count]
        if element_kind == "rec":
            return [_decode_struct(item) for item in items]
        return [_unwrap_typed({element_kind: item}) for item in items]
    return payload


def _decode_struct(value: Any) -> dict[int, Any]:
    if not isinstance(value, dict):
        return {}
    decoded: dict[int, Any] = {}
    for raw_id, typed in value.items():
        try:
            field_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        decoded[field_id] = _unwrap_typed(typed)
    return decoded


def _decode_response(text: str, expected_method: str) -> dict[int, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as err:
        raise SmartemisConnectionError("Smartemis returned invalid JSON") from err

    if not isinstance(payload, list) or len(payload) < 5:
        raise SmartemisConnectionError("Smartemis returned an invalid Thrift envelope")

    version, method, message_type, _seqid, body = payload[:5]
    if version != 1 or method != expected_method or message_type != 2:
        raise SmartemisConnectionError("Unexpected Smartemis Thrift response")

    envelope = _decode_struct(body)
    success = envelope.get(0)
    if isinstance(success, dict):
        return success

    exception = envelope.get(1)
    if isinstance(exception, dict) and isinstance(exception.get(2), dict):
        _raise_for_result(exception[2])
    raise SmartemisConnectionError("Smartemis response has no success payload")


def _raise_for_result(result: dict[int, Any]) -> None:
    code = result.get(1)
    number = result.get(2)
    message = result.get(3)
    num = int(number) if isinstance(number, int) else None
    if num == RESULT_OK:
        return
    raise SmartemisServiceError(
        num,
        str(code) if code is not None else None,
        str(message) if message is not None else None,
    )


def _ensure_success(response: dict[int, Any]) -> None:
    result = response.get(1)
    if not isinstance(result, dict):
        raise SmartemisConnectionError("Smartemis response has no ServiceCallResult")
    _raise_for_result(result)


def _status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "code": value.get(1),
        "name": value.get(2),
        "rgb": value.get(3),
    }


def _artemis_date(value: Any) -> str | None:
    return str(value.get(1)) if isinstance(value, dict) and value.get(1) else None


def _normalize_sdis(value: Any) -> SmartemisSdis | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get(1) or "").strip()
    urls = tuple(str(url).strip() for url in (value.get(2) or []) if str(url).strip())
    return SmartemisSdis(name=name, urls=urls) if name and urls else None


def _normalize_subscriber(value: Any) -> SmartemisSubscriber | None:
    if not isinstance(value, dict):
        return None
    phone_id = str(value.get(1) or "").strip()
    person_id = str(value.get(2) or "").strip()
    organization = str(value.get(3) or "").strip()
    if not phone_id or not person_id or not organization:
        return None
    return SmartemisSubscriber(
        phone_id=phone_id,
        person_id=person_id,
        organization=organization,
    )


def _normalize_center(value: Any) -> SmartemisCenter | None:
    if not isinstance(value, dict) or not isinstance(value.get(1), dict):
        return None

    person = value[1]
    center_id = str(person.get(1) or "").strip()
    matricule = str(person.get(5) or "").strip()
    if not center_id or not matricule:
        return None

    administrative_status = _status(person.get(9)) or {}
    rights = tuple(
        str(item.get(1))
        for item in (value.get(2) or [])
        if isinstance(item, dict) and item.get(1)
    )
    return SmartemisCenter(
        id=center_id,
        name=str(person.get(2) or center_id).strip(),
        matricule=matricule,
        person_id=str(person.get(6)) if person.get(6) else None,
        last_name=str(person.get(7) or "").strip(),
        first_name=str(person.get(8) or "").strip(),
        person_status_code=str(person.get(3)) if person.get(3) else None,
        administrative_status_code=(
            str(administrative_status.get("code"))
            if administrative_status.get("code")
            else None
        ),
        administrative_status_name=(
            str(administrative_status.get("name"))
            if administrative_status.get("name")
            else None
        ),
        rights=rights,
    )


def _normalize_user_context(value: Any) -> SmartemisUserContext:
    if not isinstance(value, dict):
        return SmartemisUserContext()
    return SmartemisUserContext(
        default_center_code=str(value.get(1)) if value.get(1) else None,
        display_interventions=str(value.get(4)) if value.get(4) else None,
        display_level=str(value.get(5)) if value.get(5) else None,
    )


def _normalize_period(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": _status(value.get(1)),
        "start": _artemis_date(value.get(2)),
        "end": _artemis_date(value.get(3)),
        "order": value.get(4),
    }


def _normalize_current_planning(response: dict[int, Any]) -> dict[str, Any]:
    available = response.get(6)
    return {
        "current_period": _normalize_period(response.get(3)),
        "periods": tuple(
            period
            for raw in (response.get(4) or [])
            if (period := _normalize_period(raw)) is not None
        ),
        "available_personnel": (
            available.get(1) if isinstance(available, dict) else None
        ),
    }


def _normalize_intervention(value: dict[int, Any]) -> dict[str, Any]:
    return {
        "id": value.get(1),
        "number": value.get(2),
        "title": value.get(3),
        "address": value.get(4),
        "created": _artemis_date(value.get(5)),
    }


def _normalize_intervention_detail(response: dict[int, Any]) -> dict[str, Any]:
    detail = response.get(2) if isinstance(response.get(2), dict) else {}
    vehicles = tuple(
        {
            "id": vehicle.get(3),
            "name": vehicle.get(4),
            "state_code": vehicle.get(5),
            "center_id": unit.get(3),
            "center_name": unit.get(4),
        }
        for unit in (detail.get(1) or [])
        if isinstance(unit, dict)
        for vehicle in (unit.get(5) or [])
        if isinstance(vehicle, dict)
    )
    addresses = tuple(
        {
            "type": address.get(1),
            "address": address.get(2),
            "longitude": address.get(3),
            "latitude": address.get(4),
        }
        for address in (detail.get(3) or [])
        if isinstance(address, dict)
    )
    return {"vehicles": vehicles, "addresses": addresses}


def _update_current_planning_fields(
    *,
    phone_id: str,
    center_id: str,
    matricule: str,
    status: dict[str, Any],
    period_end: str,
) -> dict[int, dict[str, Any]]:
    """Build the exact Smartemis EOP status-write request observed in the app."""

    code = str(status.get("code") or "").strip()
    if not code:
        raise ValueError("Smartemis status code is required")
    if not period_end:
        raise ValueError("Smartemis current period end is required")

    administrative_status: dict[int, dict[str, Any]] = {
        1: _scalar("str", code),
    }
    if status.get("name") is not None:
        administrative_status[2] = _scalar("str", str(status["name"]))
    if status.get("rgb") is not None:
        administrative_status[3] = _scalar("str", str(status["rgb"]))
    if status.get("order") is not None:
        administrative_status[4] = _scalar("i32", int(status["order"]))

    schedule_period = _record(
        {
            1: _record(administrative_status),
            3: _record({1: _scalar("str", period_end)}),
        }
    )
    boundary_type = _record({1: _scalar("str", BOUNDARY_END_OF_PERIOD)})

    return {
        1: _scalar("str", phone_id),
        2: _scalar("str", center_id),
        3: schedule_period,
        4: boundary_type,
        5: _scalar("str", matricule),
    }


def _center_key(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return str(int(text)) if text.isdigit() else text


def select_primary_center(
    centers: tuple[SmartemisCenter, ...],
    context: SmartemisUserContext,
) -> SmartemisCenter:
    """Select the subscriber's actual affiliated centre.

    Smartemis user context field 1 is the user's *default displayed centre*
    (the app labels it "default_cs"), not necessarily the centre where the
    subscriber is declared. getCsPersListXFct exposes that distinction through
    person_status_code: "N" means the subscriber is not declared in that
    centre, while the affiliated centre has another value (observed as "X").
    """

    if not centers:
        raise SmartemisServiceError(None, "NO_CENTER", "Smartemis exposed no centre")

    affiliated = tuple(
        center
        for center in centers
        if center.person_status_code
        and center.person_status_code.strip().upper() != "N"
    )
    if not affiliated:
        raise SmartemisServiceError(
            None,
            "NO_AFFILIATED_CENTER",
            "Smartemis exposed no centre where the subscriber is declared",
        )

    if len(affiliated) == 1:
        return affiliated[0]

    wanted = _center_key(context.default_center_code)
    if wanted:
        for center in affiliated:
            if _center_key(center.id) == wanted:
                return center

    return affiliated[0]


class SmartemisApi:
    """Minimal async Smartemis client using the app's Thrift JSON protocol."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        timeout: int = 20,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._verify_ssl = verify_ssl

    async def _call(
        self,
        url: str,
        method: str,
        fields: dict[int, dict[str, Any]],
    ) -> dict[int, Any]:
        try:
            async with self._session.post(
                url,
                data=_request(method, fields),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
                ssl=self._verify_ssl,
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise SmartemisConnectionError(
                        f"Smartemis HTTP {response.status} for {method}"
                    )
        except SmartemisError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartemisConnectionError(str(err)) from err
        return _decode_response(text, method)

    async def async_discover_sdis(self, phone_id: str) -> tuple[SmartemisSdis, ...]:
        response = await self._call(
            SMARTEMIS_DIRECTORY_URL,
            "retrieveSdisList",
            {1: _scalar("str", phone_id)},
        )
        _ensure_success(response)
        return tuple(
            sdis
            for raw in (response.get(2) or [])
            if (sdis := _normalize_sdis(raw)) is not None
        )

    async def async_get_subscriber(
        self,
        url: str,
        *,
        phone_id: str,
    ) -> SmartemisSubscriber:
        """Validate and reuse an identity already registered by the mobile app."""

        response = await self._call(
            url,
            "getSubscriber",
            {
                1: _scalar("str", phone_id),
                2: _scalar("tf", 1),
                3: _scalar("tf", 1),
                4: _scalar("tf", 1),
            },
        )
        _ensure_success(response)
        subscriber = _normalize_subscriber(response.get(2))
        if subscriber is None or subscriber.phone_id != phone_id:
            raise SmartemisConnectionError(
                "Smartemis returned an invalid subscriber identity"
            )
        return subscriber

    async def async_get_identity(
        self,
        url: str,
        *,
        phone_id: str,
    ) -> tuple[SmartemisSubscriber, SmartemisUserContext, SmartemisCenter]:
        """Resolve the existing subscriber identity and affiliated centre."""

        subscriber = await self.async_get_subscriber(url, phone_id=phone_id)
        context = await self.async_get_user_context(url, phone_id=phone_id)
        centers = await self.async_get_centers(url, phone_id=phone_id)
        return subscriber, context, select_primary_center(centers, context)

    async def async_get_user_context(
        self, url: str, *, phone_id: str
    ) -> SmartemisUserContext:
        response = await self._call(
            url,
            "getUserContext",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", SMARTEMIS_RELEASE_CONTEXT),
            },
        )
        _ensure_success(response)
        return _normalize_user_context(response.get(2))

    async def async_get_centers(
        self, url: str, *, phone_id: str
    ) -> tuple[SmartemisCenter, ...]:
        response = await self._call(
            url,
            "getCsPersListXFct",
            {1: _scalar("str", phone_id)},
        )
        _ensure_success(response)
        return tuple(
            center
            for raw in (response.get(2) or [])
            if (center := _normalize_center(raw)) is not None
        )

    async def async_get_current_planning(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str,
        matricule: str,
    ) -> dict[str, Any]:
        response = await self._call(
            url,
            "getCurrentPlanning",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", center_id),
                3: _scalar("str", matricule),
            },
        )
        _ensure_success(response)
        return _normalize_current_planning(response)

    async def async_update_current_planning(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str,
        matricule: str,
        status: dict[str, Any],
        period_end: str,
    ) -> dict[str, Any]:
        """Change current Smartemis status until the current period boundary.

        This mirrors the official app's updateCurrentPlanning request with the
        safest boundary type, EOP (end of current period). The Home Assistant
        integration does not expose this write yet; it is kept behind the
        client API until a live write/read-back validation has been completed.
        """

        response = await self._call(
            url,
            "updateCurrentPlanning",
            _update_current_planning_fields(
                phone_id=phone_id,
                center_id=center_id,
                matricule=matricule,
                status=status,
                period_end=period_end,
            ),
        )
        _ensure_success(response)
        return _normalize_current_planning(response)

    async def async_get_interventions(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str | None,
        matricule: str | None,
        display_level: str | None,
    ) -> tuple[dict[str, Any], ...]:
        response = await self._call(
            url,
            "getSyItv",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", center_id or THRIFT_NULL),
                3: _scalar("str", matricule or THRIFT_NULL),
                4: _scalar("str", display_level or THRIFT_NULL),
            },
        )
        _ensure_success(response)
        return tuple(
            _normalize_intervention(item)
            for item in (response.get(2) or [])
            if isinstance(item, dict)
        )

    async def async_get_intervention_detail(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str,
        matricule: str,
        intervention_id: int,
    ) -> dict[str, Any]:
        response = await self._call(
            url,
            "getItv",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", center_id),
                3: _scalar("str", matricule),
                4: _scalar("i32", intervention_id),
            },
        )
        _ensure_success(response)
        return _normalize_intervention_detail(response)
