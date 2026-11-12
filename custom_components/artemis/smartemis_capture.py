"""Temporary local proxy used to capture an existing Smartemis phoneId.

The proxy is deliberately narrow:
- it binds to one local IPv4 address on an ephemeral port;
- the PAC file proxies only the official Smartemis directory host;
- only one client peer is accepted during a capture session;
- only the retrieveSdisList request is inspected;
- request bodies and phoneIds are never written to logs;
- the TLS certificate/key are generated in memory and temporary files are deleted
  immediately after the SSL context has loaded them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import ssl
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from urllib.parse import urlparse

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .smartemis import SMARTEMIS_DIRECTORY_URL

_LOGGER = logging.getLogger(__name__)

_CAPTURE_TIMEOUT_SECONDS = 300
_HEADER_LIMIT = 16 * 1024
_BODY_LIMIT = 64 * 1024
_TARGET_HOST = urlparse(SMARTEMIS_DIRECTORY_URL).hostname or ""
_TARGET_PORT = 443

Forwarder = Callable[[bytes], Awaitable[tuple[int, bytes, str]]]


def _phone_id_fingerprint(phone_id: str) -> str:
    """Return a non-reversible short fingerprint suitable for debug logs."""

    return hashlib.sha256(phone_id.encode("utf-8")).hexdigest()[:12]


def _extract_retrieve_sdis_phone_id(body: bytes) -> str | None:
    """Extract phoneId only from the exact retrieveSdisList Thrift request."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None

    if (
        not isinstance(payload, list)
        or len(payload) < 5
        or payload[0] != 1
        or payload[1] != "retrieveSdisList"
        or payload[2] != 1
    ):
        return None

    try:
        phone_id = payload[4]["1"]["rec"]["1"]["str"]
    except (KeyError, TypeError):
        return None

    value = str(phone_id).strip()
    return value or None


def _discover_local_ipv4() -> str:
    """Return the IPv4 address used by the host's default route without sending data."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # TEST-NET-1 is non-routable. connect() only asks the kernel which local
        # interface it would use; no packet is transmitted.
        sock.connect(("192.0.2.1", 9))
        address = str(sock.getsockname()[0])
    finally:
        sock.close()

    if not address or address.startswith("127."):
        raise OSError("No LAN IPv4 address is available for Smartemis capture")
    return address


def _build_ephemeral_tls_context(hostname: str) -> ssl.SSLContext:
    """Create a short-lived self-signed certificate and return a TLS server context."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    cert_path = ""
    key_path = ""
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False) as cert_file:
            cert_file.write(cert_pem)
            cert_path = cert_file.name
        with tempfile.NamedTemporaryFile("wb", delete=False) as key_file:
            key_file.write(key_pem)
            key_path = key_file.name

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        return context
    finally:
        for path in (cert_path, key_path):
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes]:
    """Read one small HTTP request from a stream."""

    raw_headers = await reader.readuntil(b"\r\n\r\n")
    if len(raw_headers) > _HEADER_LIMIT:
        raise ValueError("HTTP headers exceed capture limit")

    lines = raw_headers.decode("latin-1").split("\r\n")
    request_line = lines[0].split()
    if len(request_line) < 2:
        raise ValueError("Invalid HTTP request line")
    method, target = request_line[:2]

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0") or "0")
    if content_length < 0 or content_length > _BODY_LIMIT:
        raise ValueError("HTTP body exceeds capture limit")
    body = await reader.readexactly(content_length) if content_length else b""
    return method.upper(), target, headers, body


def _http_response(
    status: int,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> bytes:
    reason = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "OK"
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body


class SmartemisCaptureProxy:
    """One-shot local HTTPS interception proxy for Smartemis directory discovery."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        bind_host: str | None = None,
        timeout_seconds: int = _CAPTURE_TIMEOUT_SECONDS,
        forwarder: Forwarder | None = None,
    ) -> None:
        self._session = session
        self._bind_host = bind_host
        self._timeout_seconds = timeout_seconds
        self._forwarder = forwarder or self._forward_directory_request
        self._server: asyncio.AbstractServer | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._tls_context: ssl.SSLContext | None = None
        self._authorized_peer: str | None = None
        self._captured_phone_id: str | None = None
        self._capture_event = asyncio.Event()
        self._port: int | None = None

    @property
    def host(self) -> str:
        if self._bind_host is None:
            raise RuntimeError("Smartemis capture proxy has not started")
        return self._bind_host

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Smartemis capture proxy has not started")
        return self._port

    @property
    def pac_url(self) -> str:
        return f"http://{self.host}:{self.port}/proxy.pac"

    @property
    def captured_phone_id(self) -> str | None:
        return self._captured_phone_id

    @property
    def captured(self) -> bool:
        return self._captured_phone_id is not None

    async def async_start(self) -> None:
        if self._server is not None:
            return

        if self._bind_host is None:
            self._bind_host = _discover_local_ipv4()

        self._tls_context = _build_ephemeral_tls_context(_TARGET_HOST)
        self._server = await asyncio.start_server(
            self._handle_client,
            self._bind_host,
            0,
            limit=_BODY_LIMIT + _HEADER_LIMIT,
        )
        socket_info = self._server.sockets[0].getsockname()
        self._port = int(socket_info[1])
        self._timeout_task = asyncio.create_task(self._auto_stop())

        _LOGGER.debug(
            "Smartemis capture started host=%s port=%s target=%s ttl=%ss "
            "storage=memory-only payload_logging=disabled phone_id_logging=hashed-only",
            self.host,
            self.port,
            _TARGET_HOST,
            self._timeout_seconds,
        )

    async def async_stop(self, *, reason: str = "requested") -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

        task = self._timeout_task
        self._timeout_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

        _LOGGER.debug(
            "Smartemis capture stopped reason=%s captured=%s peer_locked=%s",
            reason,
            self.captured,
            self._authorized_peer is not None,
        )

    async def async_wait_for_capture(self, timeout: float | None = None) -> str | None:
        if self.captured:
            return self._captured_phone_id
        try:
            await asyncio.wait_for(self._capture_event.wait(), timeout=timeout)
        except TimeoutError:
            return None
        return self._captured_phone_id

    async def _auto_stop(self) -> None:
        try:
            await asyncio.sleep(self._timeout_seconds)
            await self.async_stop(reason="timeout")
        except asyncio.CancelledError:
            return

    def _peer_allowed(self, peer: str) -> bool:
        if self._authorized_peer is None:
            self._authorized_peer = peer
            _LOGGER.debug("Smartemis capture peer locked peer=%s", peer)
            return True
        return peer == self._authorized_peer

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer_info = writer.get_extra_info("peername")
        peer = str(peer_info[0]) if peer_info else "unknown"
        try:
            method, target, _headers, _body = await _read_http_request(reader)
            _LOGGER.debug(
                "Smartemis capture plain request peer=%s method=%s target=%s",
                peer,
                method,
                target.split("?", 1)[0],
            )

            if method == "GET" and target.split("?", 1)[0] == "/proxy.pac":
                if not self._peer_allowed(peer):
                    writer.write(_http_response(403, b"Forbidden", content_type="text/plain"))
                else:
                    pac = (
                        "function FindProxyForURL(url, host) {"
                        f' if (host === "{_TARGET_HOST}") return "PROXY {self.host}:{self.port}";'
                        ' return "DIRECT";'
                        "}"
                    ).encode()
                    writer.write(
                        _http_response(
                            200,
                            pac,
                            content_type="application/x-ns-proxy-autoconfig",
                        )
                    )
                await writer.drain()
                return

            if method != "CONNECT":
                writer.write(_http_response(405, b"Method Not Allowed", content_type="text/plain"))
                await writer.drain()
                return

            if not self._peer_allowed(peer):
                _LOGGER.debug("Smartemis capture CONNECT rejected peer=%s reason=peer_mismatch", peer)
                writer.write(_http_response(403, b"Forbidden", content_type="text/plain"))
                await writer.drain()
                return

            connect_host, _, connect_port = target.partition(":")
            if connect_host.lower() != _TARGET_HOST or connect_port not in {"", str(_TARGET_PORT)}:
                _LOGGER.debug(
                    "Smartemis capture CONNECT rejected peer=%s host=%s reason=target_not_allowed",
                    peer,
                    connect_host,
                )
                writer.write(_http_response(403, b"Forbidden", content_type="text/plain"))
                await writer.drain()
                return

            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            if self._tls_context is None:
                raise RuntimeError("Capture TLS context is unavailable")
            await writer.start_tls(self._tls_context)
            _LOGGER.debug(
                "Smartemis capture TLS established peer=%s host=%s",
                peer,
                connect_host,
            )

            inner_method, inner_target, headers, body = await _read_http_request(reader)
            _LOGGER.debug(
                "Smartemis capture HTTPS request peer=%s method=%s path=%s bytes=%s content_type=%s",
                peer,
                inner_method,
                inner_target.split("?", 1)[0],
                len(body),
                headers.get("content-type", ""),
            )

            phone_id = _extract_retrieve_sdis_phone_id(body)
            if phone_id is not None:
                self._captured_phone_id = phone_id
                self._capture_event.set()
                _LOGGER.debug(
                    "Smartemis phoneId captured peer=%s fingerprint=%s length=%s "
                    "raw_value_logged=false",
                    peer,
                    _phone_id_fingerprint(phone_id),
                    len(phone_id),
                )

            if inner_method != "POST":
                writer.write(_http_response(405, b"Method Not Allowed", content_type="text/plain"))
                await writer.drain()
                return

            status, response_body, content_type = await self._forwarder(body)
            _LOGGER.debug(
                "Smartemis directory response forwarded peer=%s status=%s bytes=%s",
                peer,
                status,
                len(response_body),
            )
            writer.write(
                _http_response(status, response_body, content_type=content_type)
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            _LOGGER.debug("Smartemis capture client disconnected peer=%s", peer)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Smartemis capture client error peer=%s type=%s",
                peer,
                type(err).__name__,
            )
            try:
                writer.write(_http_response(502, b"Bad Gateway", content_type="text/plain"))
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _forward_directory_request(
        self,
        body: bytes,
    ) -> tuple[int, bytes, str]:
        """Forward the original directory request directly to the official endpoint."""

        _LOGGER.debug(
            "Smartemis directory forward start target=%s request_bytes=%s "
            "request_body_logged=false",
            _TARGET_HOST,
            len(body),
        )
        try:
            async with self._session.post(
                SMARTEMIS_DIRECTORY_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response_body = await response.read()
                content_type = response.headers.get(
                    "Content-Type", "application/json"
                ).split(";", 1)[0]
                return response.status, response_body, content_type
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConnectionError(
                f"Smartemis directory forwarding failed: {type(err).__name__}"
            ) from err
