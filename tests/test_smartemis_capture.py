from __future__ import annotations

import asyncio
import importlib
import ssl
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM = ROOT / "custom_components"
ARTEMIS = CUSTOM / "artemis"

custom_pkg = types.ModuleType("custom_components")
custom_pkg.__path__ = [str(CUSTOM)]
sys.modules.setdefault("custom_components", custom_pkg)
artemis_pkg = types.ModuleType("custom_components.artemis")
artemis_pkg.__path__ = [str(ARTEMIS)]
sys.modules.setdefault("custom_components.artemis", artemis_pkg)

capture = importlib.import_module("custom_components.artemis.smartemis_capture")
smartemis = importlib.import_module("custom_components.artemis.smartemis")


def _directory_request(phone_id: str) -> bytes:
    return smartemis._request(
        "retrieveSdisList",
        {1: smartemis._scalar("str", phone_id)},
    ).encode()


class CaptureParserTests(unittest.TestCase):
    def test_extracts_only_retrieve_sdis_phone_id(self) -> None:
        self.assertEqual(
            capture._extract_retrieve_sdis_phone_id(
                _directory_request("existing-phone-id")
            ),
            "existing-phone-id",
        )
        self.assertIsNone(
            capture._extract_retrieve_sdis_phone_id(
                smartemis._request(
                    "getSubscriber",
                    {1: smartemis._scalar("str", "existing-phone-id")},
                ).encode()
            )
        )
        self.assertIsNone(capture._extract_retrieve_sdis_phone_id(b"not-json"))

    def test_log_fingerprint_is_non_reversible_representation(self) -> None:
        phone_id = "existing-phone-id"
        fingerprint = capture._phone_id_fingerprint(phone_id)
        self.assertEqual(len(fingerprint), 12)
        self.assertNotIn(phone_id, fingerprint)


class CaptureProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.forwarded: list[bytes] = []

        async def forwarder(body: bytes) -> tuple[int, bytes, str]:
            self.forwarded.append(body)
            return (
                200,
                b'[1,"retrieveSdisList",2,0,{"0":{"rec":{"1":{"rec":{"1":{"str":"OK"},"2":{"i32":0}}},"2":{"lst":["rec",0]}}}}}]',
                "application/json",
            )

        self.proxy = capture.SmartemisCaptureProxy(
            object(),
            bind_host="127.0.0.1",
            timeout_seconds=30,
            forwarder=forwarder,
        )
        await self.proxy.async_start()

    async def asyncTearDown(self) -> None:
        await self.proxy.async_stop(reason="test_cleanup")

    async def test_pac_proxies_only_smartemis_directory(self) -> None:
        reader, writer = await asyncio.open_connection(
            self.proxy.host, self.proxy.port
        )
        writer.write(
            b"GET /proxy.pac HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        text = response.decode("latin-1")
        self.assertIn("200 OK", text)
        self.assertIn("smartemis.artemis-solutions.net", text)
        self.assertIn(f"PROXY {self.proxy.host}:{self.proxy.port}", text)
        self.assertIn('return "DIRECT"', text)

    async def test_tls_capture_extracts_and_forwards_without_mutation(self) -> None:
        phone_id = "existing-phone-id"
        body = _directory_request(phone_id)

        log_context = self.assertLogs(
            "custom_components.artemis.smartemis_capture",
            level="DEBUG",
        )
        logs = log_context.__enter__()

        reader, writer = await asyncio.open_connection(
            self.proxy.host, self.proxy.port
        )
        writer.write(
            b"CONNECT smartemis.artemis-solutions.net:443 HTTP/1.1\r\n"
            b"Host: smartemis.artemis-solutions.net:443\r\n\r\n"
        )
        await writer.drain()
        connect_response = await reader.readuntil(b"\r\n\r\n")
        self.assertIn(b"200 Connection Established", connect_response)

        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        await writer.start_tls(
            client_context,
            server_hostname="smartemis.artemis-solutions.net",
        )

        writer.write(
            b"POST /subscription/ HTTP/1.1\r\n"
            b"Host: smartemis.artemis-solutions.net\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        response = await reader.read()

        writer.close()
        await writer.wait_closed()

        self.assertIn(b"200 OK", response)
        self.assertEqual(self.forwarded, [body])
        self.assertEqual(self.proxy.captured_phone_id, phone_id)
        self.assertEqual(
            await self.proxy.async_wait_for_capture(timeout=0.1),
            phone_id,
        )
        log_context.__exit__(None, None, None)
        joined_logs = "\n".join(logs.output)
        self.assertNotIn(phone_id, joined_logs)
        self.assertIn(capture._phone_id_fingerprint(phone_id), joined_logs)

    async def test_rejects_unrelated_connect_target(self) -> None:
        reader, writer = await asyncio.open_connection(
            self.proxy.host, self.proxy.port
        )
        writer.write(
            b"CONNECT example.com:443 HTTP/1.1\r\n"
            b"Host: example.com:443\r\n\r\n"
        )
        await writer.drain()
        response = await reader.read()

        writer.close()
        await writer.wait_closed()

        self.assertIn(b"403 Forbidden", response)
        self.assertFalse(self.proxy.captured)


if __name__ == "__main__":
    unittest.main()
