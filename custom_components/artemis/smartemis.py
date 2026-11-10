"""Minimal Smartemis Apache Thrift JSON client.

The Smartemis Android app (6.0.22) uses Apache Thrift JSON over HTTPS. This
module implements only the subset needed for discovery/authentication and the
read-only proof of concept; it deliberately avoids any planning write method.

The Android app explicitly calls ``setServerTrustMode("nocheck")`` before its
native Smartemis requests. Some deployments (including the SIM Montreal test
server) therefore work in the official app while presenting a certificate
chain that normal Python TLS validation rejects. The client mirrors that app
behaviour by default; callers can opt back into normal certificate validation
with ``verify_ssl=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import aiohttp

SMARTEMIS_DIRECTORY_URL = "https://smartemis.artemis-solutions.net/subscription/"
SMARTEMIS_RELEASE_CONTEXT = "2017"

RESULT_OK = 0
RESULT_AUTHENTICATION_NEEDED = 1

RIGHT_INTERVENTIONS = "SMART:FCT:SY:INTV"
RIGHT_PARAMETERS = "SMART:FCT:PARAM"
YES = "O"


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
    """One Smartemis server advertised by the central directory."""

    name: str
    urls: tuple[str, ...]
    logo_url: str | None = None
    timeout: int | None = None
    supports_authority: bool = False
    language: str | None = None


@dataclass(frozen=True, slots=True)
class SmartemisCenter:
    """One centre/profile returned for the registered subscriber."""

    id: str
    name: str
    matricule: str
    person_id: str | None
    status_code: str | None
    status_name: str | None
    rights: tuple[str, ...] = ()

    @property
    def can_view_interventions(self) -> bool | None:
        """Return the app-advertised intervention right when rights are known."""
        if not self.rights:
            return None
        return RIGHT_INTERVENTIONS in self.rights


@dataclass(frozen=True, slots=True)
class SmartemisUserContext:
    """Subset of Smartemis user context relevant to read-only data retrieval."""

    center_code: str | None = None
    profile_code: str | None = None
    launch_page_code: str | None = None
    display_interventions: str | None = None
    display_level: str | None = None
    severity_levels: tuple[tuple[str | None, str | None], ...] = ()
    delete_media: str | None = None


def _scalar(kind: str, value: Any) -> dict[str, Any]:
    return {kind: value}


def _record(fields: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {"rec": {str(field_id): value for field_id, value in fields.items()}}


def _request(method: str, fields: dict[int, dict[str, Any]]) -> str:
    # Thrift JSON protocol v1, CALL=1, sequence id 0. Every Smartemis method
    # takes one request struct as argument field 1.
    body = [1, method, 1, 0, {"1": _record(fields)}]
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def _unwrap_typed(value: Any) -> Any:
    """Decode a Thrift JSON typed value into field-id dictionaries/lists."""
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
        raw_items = payload[2 : 2 + count]
        if element_kind == "rec":
            return [_decode_struct(item) for item in raw_items]
        return [_unwrap_typed({element_kind: item}) for item in raw_items]
    if kind == "map":
        return payload
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
    if version != 1 or method != expected_method:
        raise SmartemisConnectionError("Unexpected Smartemis Thrift response")
    if message_type != 2:
        raise SmartemisConnectionError(f"Smartemis Thrift message type {message_type}")
    envelope = _decode_struct(body)
    success = envelope.get(0)
    if not isinstance(success, dict):
        exception = envelope.get(1)
        if isinstance(exception, dict):
            call_result = exception.get(2)
            if isinstance(call_result, dict):
                _raise_for_result(call_result)
        raise SmartemisConnectionError("Smartemis response has no success payload")
    return success


def _raise_for_result(
    result: dict[int, Any], *, allow_auth_needed: bool = False
) -> int | None:
    code = result.get(1)
    num = result.get(2)
    lib = result.get(3)
    number = int(num) if isinstance(num, int) else None
    if number == RESULT_OK:
        return number
    if allow_auth_needed and number == RESULT_AUTHENTICATION_NEEDED:
        return number
    raise SmartemisServiceError(
        number,
        str(code) if code is not None else None,
        str(lib) if lib is not None else None,
    )


def _ensure_success(
    response: dict[int, Any], *, allow_auth_needed: bool = False
) -> int | None:
    result = response.get(1)
    if not isinstance(result, dict):
        raise SmartemisConnectionError("Smartemis response has no ServiceCallResult")
    return _raise_for_result(result, allow_auth_needed=allow_auth_needed)


def _right_codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get(1))
        for item in value
        if isinstance(item, dict) and item.get(1)
    )


def _normalize_user_context(value: Any) -> SmartemisUserContext:
    if not isinstance(value, dict):
        return SmartemisUserContext()
    severity_levels = tuple(
        (
            str(item.get(1)) if item.get(1) else None,
            str(item.get(2)) if item.get(2) else None,
        )
        for item in (value.get(7) or [])
        if isinstance(item, dict)
    )
    return SmartemisUserContext(
        center_code=str(value.get(1)) if value.get(1) else None,
        profile_code=str(value.get(2)) if value.get(2) else None,
        launch_page_code=str(value.get(3)) if value.get(3) else None,
        display_interventions=str(value.get(4)) if value.get(4) else None,
        display_level=str(value.get(5)) if value.get(5) else None,
        severity_levels=severity_levels,
        delete_media=str(value.get(8)) if value.get(8) else None,
    )


class SmartemisApi:
    """Async subset of the Smartemis MCentralService/MArtemisService API."""

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

    @property
    def verify_ssl(self) -> bool:
        """Whether Smartemis TLS certificates are verified."""
        return self._verify_ssl

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
        raw_sdis = response.get(2) or []
        result: list[SmartemisSdis] = []
        for item in raw_sdis if isinstance(raw_sdis, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get(1) or "").strip()
            urls_raw = item.get(2) or []
            urls = tuple(str(url).strip() for url in urls_raw if str(url).strip())
            if not name or not urls:
                continue
            result.append(
                SmartemisSdis(
                    name=name,
                    urls=urls,
                    logo_url=str(item.get(3)) if item.get(3) else None,
                    timeout=int(item[4]) if isinstance(item.get(4), int) else None,
                    supports_authority=bool(item.get(5)),
                    language=str(item.get(6)) if item.get(6) else None,
                )
            )
        return tuple(result)

    async def async_record_subscriber(
        self,
        url: str,
        *,
        phone_id: str,
        person_id: str,
        mobile: str,
        organization: str,
        authority: bool = False,
    ) -> bool:
        subscriber = _record(
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", person_id),
                3: _scalar("str", organization),
                4: _scalar("tf", 1 if authority else 0),
            }
        )
        response = await self._call(
            url,
            "recordSubscriber",
            {
                1: _scalar("str", mobile),
                2: subscriber,
                3: _scalar("tf", 1),
                4: _scalar("tf", 1),
                5: _scalar("tf", 1),
            },
        )
        result_num = _ensure_success(response, allow_auth_needed=True)
        return result_num == RESULT_AUTHENTICATION_NEEDED

    async def async_authentify(
        self, url: str, *, phone_id: str, password: str
    ) -> None:
        response = await self._call(
            url,
            "authentify",
            {1: _scalar("str", phone_id), 2: _scalar("str", password)},
        )
        _ensure_success(response)

    async def async_get_centers(
        self, url: str, *, phone_id: str
    ) -> tuple[SmartemisCenter, ...]:
        response = await self._call(
            url,
            "getCsPersListXFct",
            {1: _scalar("str", phone_id)},
        )
        _ensure_success(response)
        raw_centers = response.get(2) or []
        centers: list[SmartemisCenter] = []
        for right in raw_centers if isinstance(raw_centers, list) else []:
            if not isinstance(right, dict):
                continue
            person = right.get(1)
            if not isinstance(person, dict):
                continue
            center_id = str(person.get(1) or "").strip()
            matricule = str(person.get(5) or "").strip()
            if not center_id or not matricule:
                continue
            centers.append(
                SmartemisCenter(
                    id=center_id,
                    name=str(person.get(2) or center_id),
                    matricule=matricule,
                    person_id=str(person.get(6)) if person.get(6) else None,
                    status_code=str(person.get(3)) if person.get(3) else None,
                    status_name=str(person.get(4)) if person.get(4) else None,
                    rights=_right_codes(right.get(2)),
                )
            )
        return tuple(centers)

    async def async_get_user_context(
        self,
        url: str,
        *,
        phone_id: str,
        release_context: str = SMARTEMIS_RELEASE_CONTEXT,
    ) -> SmartemisUserContext:
        response = await self._call(
            url,
            "getUserContext",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", release_context),
            },
        )
        _ensure_success(response)
        return _normalize_user_context(response.get(2))

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

    async def async_get_interventions(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str,
        matricule: str,
        display_level: str = "null",
        global_view: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        response = await self._call(
            url,
            "getSyItv",
            {
                1: _scalar("str", phone_id),
                2: _scalar("str", "null" if global_view else center_id),
                3: _scalar("str", "null" if global_view else matricule),
                4: _scalar("str", display_level if global_view else "null"),
            },
        )
        _ensure_success(response)
        raw_items = response.get(2) or []
        return tuple(
            _normalize_intervention(item)
            for item in raw_items
            if isinstance(item, dict)
        )

    async def async_get_interventions_for_center(
        self,
        url: str,
        *,
        phone_id: str,
        center: SmartemisCenter,
    ) -> tuple[dict[str, Any], ...]:
        """Mirror the Smartemis app's intervention request selection.

        The app switches to a global/severity-filtered request only when the
        subscriber has the PARAMETRES right and the user context has displayItv=O.
        Otherwise it requests interventions for the selected centre/person.
        """
        context = await self.async_get_user_context(url, phone_id=phone_id)
        global_view = (
            RIGHT_PARAMETERS in center.rights
            and context.display_interventions == YES
        )
        return await self.async_get_interventions(
            url,
            phone_id=phone_id,
            center_id=center.id,
            matricule=center.matricule,
            display_level=context.display_level or "null",
            global_view=global_view,
        )


    async def async_get_visible_interventions(
        self,
        url: str,
        *,
        phone_id: str,
        centers: tuple[SmartemisCenter, ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return every intervention visible to the subscriber.

        The Smartemis UI normally works with one selected centre. For a
        headless Home Assistant integration there is no such selection, so
        querying only centers[0] can miss incidents attached to another
        caserne. This helper mirrors the app's global/severity mode when it is
        available; otherwise it queries every centre that advertises the
        intervention right and de-duplicates incidents by id.
        """
        if centers is None:
            centers = await self.async_get_centers(url, phone_id=phone_id)

        explicit = tuple(
            center
            for center in centers
            if RIGHT_INTERVENTIONS in center.rights
        )
        eligible = explicit or tuple(center for center in centers if not center.rights)
        if not eligible:
            return ()

        context = await self.async_get_user_context(url, phone_id=phone_id)
        global_center = next(
            (
                center
                for center in eligible
                if RIGHT_PARAMETERS in center.rights
                and context.display_interventions == YES
            ),
            None,
        )

        if global_center is not None:
            rows = await self.async_get_interventions(
                url,
                phone_id=phone_id,
                center_id=global_center.id,
                matricule=global_center.matricule,
                display_level=context.display_level or "null",
                global_view=True,
            )
        else:
            collected: list[dict[str, Any]] = []
            for center in eligible:
                collected.extend(
                    await self.async_get_interventions(
                        url,
                        phone_id=phone_id,
                        center_id=center.id,
                        matricule=center.matricule,
                        display_level="null",
                        global_view=False,
                    )
                )
            rows = tuple(collected)

        result: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for item in rows:
            intervention_id = item.get("id")
            key = ("id", intervention_id) if intervention_id is not None else (
                "fallback",
                item.get("number"),
                item.get("title"),
                item.get("created"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return tuple(result)

    async def async_get_intervention_detail(
        self,
        url: str,
        *,
        phone_id: str,
        center_id: str,
        matricule: str,
        intervention_id: int,
    ) -> dict[str, Any]:
        """Return the detail payload used by the Smartemis intervention page."""
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


def _artemis_date(value: Any) -> str | None:
    return str(value.get(1)) if isinstance(value, dict) and value.get(1) else None


def _status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "code": value.get(1),
        "name": value.get(2),
        "rgb": value.get(3),
        "order": value.get(4),
    }


def _period(value: Any) -> dict[str, Any] | None:
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
        "server_time": _artemis_date(response.get(2)),
        "current_period": _period(response.get(3)),
        "periods": tuple(
            period
            for raw in (response.get(4) or [])
            if (period := _period(raw)) is not None
        ),
        "allowed_statuses": tuple(
            status
            for raw in (response.get(5) or [])
            if (status := _status(raw)) is not None
        ),
        "available_personnel": (
            available.get(1) if isinstance(available, dict) else None
        ),
        "max_personnel": available.get(2) if isinstance(available, dict) else None,
        "priority": response.get(7),
    }



def _normalize_intervention_detail(response: dict[int, Any]) -> dict[str, Any]:
    detail = response.get(2) if isinstance(response.get(2), dict) else {}
    base = response.get(3)
    units: list[dict[str, Any]] = []
    vehicles: list[dict[str, Any]] = []

    for unit in detail.get(1) or []:
        if not isinstance(unit, dict):
            continue
        unit_vehicles: list[dict[str, Any]] = []
        for vehicle in unit.get(5) or []:
            if not isinstance(vehicle, dict):
                continue
            normalized_vehicle = {
                "id": vehicle.get(3),
                "name": vehicle.get(4),
                "state_code": vehicle.get(5),
                "state_background": vehicle.get(6),
                "state_foreground": vehicle.get(7),
                "center_id": unit.get(3),
                "center_name": unit.get(4),
            }
            unit_vehicles.append(normalized_vehicle)
            vehicles.append(normalized_vehicle)
        units.append(
            {
                "id": unit.get(3),
                "name": unit.get(4),
                "vehicles": tuple(unit_vehicles),
            }
        )

    addresses = tuple(
        {
            "type": address.get(1),
            "address": address.get(2),
            # The official app passes [coordY, coordX] to the navigator:
            # coordX is longitude and coordY is latitude.
            "longitude": address.get(3),
            "latitude": address.get(4),
        }
        for address in (detail.get(3) or [])
        if isinstance(address, dict)
    )

    return {
        "intervention": (
            _normalize_intervention(base) if isinstance(base, dict) else None
        ),
        "units": tuple(units),
        "vehicles": tuple(vehicles),
        "addresses": addresses,
    }



def _normalize_intervention(value: dict[int, Any]) -> dict[str, Any]:
    return {
        "id": value.get(1),
        "number": value.get(2),
        "title": value.get(3),
        "address": value.get(4),
        "created": _artemis_date(value.get(5)),
        "type": value.get(6),
        "url": value.get(7),
        "vehicle_count": value.get(8),
        "address_count": value.get(9),
        "person_count": value.get(10),
        "radio_group": value.get(11),
    }
