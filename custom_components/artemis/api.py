"""HTTP client for the ARTEMIS WebEvo endpoints observed in SDIS 39."""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .const import DEFAULT_CAS_SERVICE


class ArtemisError(Exception):
    """Base ARTEMIS error."""


class ArtemisAuthError(ArtemisError):
    """ARTEMIS authentication failed."""


class ArtemisConnectionError(ArtemisError):
    """ARTEMIS could not be reached."""


class ArtemisWriteError(ArtemisError):
    """ARTEMIS rejected or could not safely complete a planning write."""


class _LtParser(HTMLParser):
    """Extract the CAS login ticket field without third-party HTML parsers."""

    def __init__(self) -> None:
        super().__init__()
        self.lt: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        if values.get("name") == "lt" and values.get("value"):
            self.lt = values["value"]


class ArtemisApi:
    """Minimal async ARTEMIS WebEvo client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
        *,
        cas_service: str = DEFAULT_CAS_SERVICE,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._cas_service = cas_service
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self.unit_id: str | None = None
        self.profile_id: str | None = None
        self.sso_name: str | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _url(self, path: str) -> str:
        return urljoin(self._base_url + "/", path.lstrip("/"))

    async def _read_text(self, response: aiohttp.ClientResponse) -> str:
        try:
            return await response.text()
        except UnicodeDecodeError:
            return await response.text(encoding="iso-8859-1", errors="replace")

    async def async_login(self, *, force: bool = False) -> None:
        """Authenticate through CAS, then select the ARTEMIS centre/profile."""
        async with self._login_lock:
            if self._logged_in and not force:
                return

            self._logged_in = False
            self._session.cookie_jar.clear()

            try:
                async with self._session.get(
                    self._url("/cas/login"),
                    params={"service": self._cas_service},
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    login_page = await self._read_text(response)
                    if response.status >= 400:
                        raise ArtemisConnectionError(f"CAS GET returned HTTP {response.status}")

                parser = _LtParser()
                parser.feed(login_page)
                if not parser.lt:
                    raise ArtemisAuthError("CAS login ticket (lt) was not found")

                async with self._session.post(
                    self._url("/cas/login"),
                    params={"service": self._cas_service},
                    data={
                        "username": self._username,
                        "password": self._password,
                        "lt": parser.lt,
                    },
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    web_login_page = await self._read_text(response)
                    if response.status >= 400:
                        raise ArtemisConnectionError(f"CAS POST returned HTTP {response.status}")

                # After CAS, WebEvo renders an SSO-backed login page containing
                # the name and one-time SHA1 value used by its own login endpoints.
                name_match = re.search(r"var\s+ssoName\s*=\s*['\"]([^'\"]+)['\"]", web_login_page)
                sha_match = re.search(r"var\s+ssoSha1\s*=\s*['\"]([^'\"]+)['\"]", web_login_page)
                if not name_match or not sha_match:
                    raise ArtemisAuthError("ARTEMIS SSO hand-off was not found after CAS login")

                sso_name = name_match.group(1)
                sso_sha1 = sha_match.group(1)

                units = await self._direct_json(
                    "POST",
                    "/artemis-web/api/login/userUnits",
                    json={"name": sso_name, "pwd": sso_sha1},
                )
                if not isinstance(units, list) or not units:
                    raise ArtemisAuthError("No ARTEMIS centre is available for this account")
                unit_id = str(units[0].get("code") or "")
                if not unit_id:
                    raise ArtemisAuthError("ARTEMIS returned an invalid centre")

                profiles = await self._direct_json(
                    "POST",
                    "/artemis-web/api/login/userProfiles",
                    json={"name": sso_name, "pwd": sso_sha1, "unit": unit_id},
                )
                if not isinstance(profiles, list) or not profiles:
                    raise ArtemisAuthError("No ARTEMIS profile is available for this account")
                profile_id = str(profiles[0].get("code") or "")
                if not profile_id:
                    raise ArtemisAuthError("ARTEMIS returned an invalid profile")

                async with self._session.post(
                    self._url("/artemis-web/page/loginSubmit"),
                    data={
                        "name": sso_name,
                        "unit": unit_id,
                        "profile": profile_id,
                        "pwd": sso_sha1,
                    },
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    final_text = await self._read_text(response)
                    if response.status >= 400:
                        raise ArtemisConnectionError(f"WebEvo login returned HTTP {response.status}")
                    if "/page/login" in str(response.url) and "dashboard" not in str(response.url):
                        # A successful login lands on dashboard. If WebEvo sent us
                        # back to its login page, credentials/session were rejected.
                        if "Connexion Artemis WebEvo" in final_text:
                            raise ArtemisAuthError("ARTEMIS WebEvo rejected the login")

                self.sso_name = sso_name
                self.unit_id = unit_id
                self.profile_id = profile_id
                self._logged_in = True

            except ArtemisError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise ArtemisConnectionError(str(err)) from err

    async def _direct_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform a JSON request during the login sequence."""
        try:
            async with self._session.request(
                method,
                self._url(path),
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20),
                **kwargs,
            ) as response:
                if response.status in {401, 403}:
                    raise ArtemisAuthError(f"HTTP {response.status}")
                if response.status >= 400:
                    raise ArtemisConnectionError(f"HTTP {response.status} for {path}")
                return await response.json(content_type=None)
        except ArtemisError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArtemisConnectionError(str(err)) from err

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Perform an authenticated JSON request, refreshing the session once."""
        if not self._logged_in:
            await self.async_login()

        try:
            async with self._session.request(
                method,
                self._url(path),
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20),
                **kwargs,
            ) as response:
                is_auth_redirect = response.status in {301, 302, 303, 307, 308}
                if response.status in {401, 403} or is_auth_redirect:
                    if retry_auth:
                        await self.async_login(force=True)
                        return await self._request_json(
                            method,
                            path,
                            retry_auth=False,
                            **kwargs,
                        )
                    raise ArtemisAuthError("ARTEMIS session expired and could not be renewed")

                if response.status >= 400:
                    raise ArtemisConnectionError(f"HTTP {response.status} for {path}")

                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type.lower():
                    if retry_auth:
                        await self.async_login(force=True)
                        return await self._request_json(
                            method,
                            path,
                            retry_auth=False,
                            **kwargs,
                        )
                    raise ArtemisAuthError("ARTEMIS returned the login page instead of JSON")

                return await response.json(content_type=None)

        except ArtemisError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArtemisConnectionError(str(err)) from err

    async def async_person_init(self) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/artemis-web/api/personPlanning/initData",
            json={},
        )
        if not isinstance(data, dict):
            raise ArtemisConnectionError("Unexpected personPlanning/initData response")
        return data

    async def async_person_planning(
        self,
        unit_id: str,
        staff_id: str,
        week_start: str,
    ) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/artemis-web/api/personPlanning/getPlanning",
            json={
                "fireunitId": unit_id,
                "staffId": staff_id,
                "date": f"{week_start}T00:00:00.000Z",
            },
        )
        if not isinstance(data, dict):
            raise ArtemisConnectionError("Unexpected personPlanning/getPlanning response")
        return data


    async def async_save_staff(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save one personal planning day using WebEvo's planning editor endpoint."""
        data = await self._request_json(
            "POST",
            "/artemis-web/api/planningStaff/saveStaff",
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if not isinstance(data, dict):
            raise ArtemisWriteError("Unexpected planningStaff/saveStaff response")
        if data.get("ubiquityPlanning"):
            # WebEvo normally opens an interactive confirmation dialog here.
            # The integration deliberately refuses to force that conflict.
            raise ArtemisWriteError(
                "ARTEMIS requires an ubiquity confirmation; no change was forced"
            )
        if not isinstance(data.get("staffMember"), dict):
            raise ArtemisWriteError("ARTEMIS did not confirm the saved planning row")
        return data

    async def async_planning_counters(
        self,
        planning_id: int | str,
        unit_id: str,
        date_value: str,
    ) -> dict[str, Any]:
        """Return the native ARTEMIS staffing counters for a centre and time."""
        data = await self._request_json(
            "POST",
            "/artemis-web/api/planningCounters/getCounters",
            json={
                "planningId": planning_id,
                "fireunitId": unit_id,
                "date": date_value,
            },
        )
        if not isinstance(data, dict):
            raise ArtemisConnectionError(
                "Unexpected planningCounters/getCounters response"
            )
        return data

    async def async_operations_init(self) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            "/artemis-web/api/synopticOperations/initData",
        )
        if not isinstance(data, dict):
            raise ArtemisConnectionError("Unexpected synopticOperations/initData response")
        return data

    async def async_operations(self) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            "/artemis-web/api/synopticOperations",
            params={"windowUid": "home-assistant"},
        )
        if not isinstance(data, dict):
            raise ArtemisConnectionError("Unexpected synopticOperations response")
        return data
