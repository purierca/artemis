"""Serve generic coloured ARTEMIS map marker images."""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

MARKER_ASSET_VERSION = 1
MARKER_URL_PREFIX = f"/api/artemis/marker/v{MARKER_ASSET_VERSION}"

MARKER_KINDS = frozenset(
    {
        "fire",
        "traffic",
        "elevator",
        "fall",
        "medical",
        "water",
        "gas",
        "animal",
        "building",
        "storm",
        "generic",
    }
)

_ACTIVE_COLORS = {
    "fire": "#D32F2F",
    "traffic": "#F57C00",
    "elevator": "#546E7A",
    "fall": "#8E24AA",
    "medical": "#1976D2",
    "water": "#0288D1",
    "gas": "#F9A825",
    "animal": "#6D4C41",
    "building": "#7E57C2",
    "storm": "#388E3C",
    "generic": "#455A64",
}
_CLOSED_COLOR = "#757575"

_GLYPHS = {
    "fire": (
        '<path fill="#fff" d="M25 7c2 7-5 10-5 16 0 3 2 5 4 6-1-4 2-6 4-9 '
        '1 3 4 5 4 10 0 7-5 12-12 12s-12-5-12-12c0-8 6-12 10-18 '
        '0 5 1 8 4 10-1-6 2-10 3-15Z"/>'
    ),
    "traffic": (
        '<path fill="none" stroke="#fff" stroke-width="3" stroke-linejoin="round" '
        'd="M13 29h22l-3-10H16l-3 10Zm3-10 3-5h10l3 5"/>'
        '<circle cx="17" cy="32" r="3" fill="#fff"/>'
        '<circle cx="31" cy="32" r="3" fill="#fff"/>'
    ),
    "elevator": (
        '<rect x="14" y="10" width="20" height="28" rx="2" fill="none" '
        'stroke="#fff" stroke-width="3"/>'
        '<path fill="#fff" d="m20 19 4-5 4 5h-3v5h-2v-5h-3Zm0 10h3v-5h2v5h3l-4 5-4-5Z"/>'
    ),
    "fall": (
        '<circle cx="20" cy="12" r="4" fill="#fff"/>'
        '<path fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" '
        'stroke-linejoin="round" d="m21 18 6 6 7 1m-7-1-4 8-7 5m7-5 8 5m-10-19-6 7-5 2"/>'
    ),
    "medical": (
        '<path fill="#fff" d="M20 9h8v11h11v8H28v11h-8V28H9v-8h11V9Z"/>'
    ),
    "water": (
        '<path fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" '
        'd="M8 17c4-4 8 4 12 0s8 4 12 0 8 4 8 4M8 25c4-4 8 4 12 0s8 4 12 0 8 4 8 4M8 33c4-4 8 4 12 0s8 4 12 0 8 4 8 4"/>'
    ),
    "gas": (
        '<rect x="16" y="13" width="16" height="26" rx="6" fill="none" '
        'stroke="#fff" stroke-width="3"/>'
        '<path fill="none" stroke="#fff" stroke-width="3" d="M20 13V9h8v4m-8 8h8"/>'
    ),
    "animal": (
        '<circle cx="16" cy="17" r="4" fill="#fff"/>'
        '<circle cx="25" cy="13" r="4" fill="#fff"/>'
        '<circle cx="34" cy="17" r="4" fill="#fff"/>'
        '<circle cx="12" cy="26" r="4" fill="#fff"/>'
        '<path fill="#fff" d="M24 21c-7 0-12 6-10 12 1 4 5 5 10 2 5 3 9 2 10-2 2-6-3-12-10-12Z"/>'
    ),
    "building": (
        '<path fill="none" stroke="#fff" stroke-width="3" stroke-linejoin="round" '
        'd="M10 23 24 11l14 12v15H10V23Zm10 15V27h8v11"/>'
        '<path fill="#fff" d="M34 12h4v10h-4zm0 12h4v4h-4z"/>'
    ),
    "storm": (
        '<path fill="#fff" d="M13 27a7 7 0 0 1 2-13 10 10 0 0 1 19 3 6 6 0 0 1 1 12H13Z"/>'
        '<path fill="#fff" d="m25 27-7 11h6l-2 7 9-13h-6l3-5h-3Z"/>'
    ),
    "generic": (
        '<path fill="none" stroke="#fff" stroke-width="3" stroke-linejoin="round" '
        'd="M24 8 41 39H7L24 8Z"/>'
        '<path fill="#fff" d="M22 18h4v11h-4zm0 14h4v4h-4z"/>'
    ),
}


def marker_picture_url(kind: str, active: bool) -> str:
    """Return the stable local URL for an ARTEMIS marker."""

    safe_kind = kind if kind in MARKER_KINDS else "generic"
    status = "live" if active else "closed"
    return f"{MARKER_URL_PREFIX}/{status}/{safe_kind}.svg"


def marker_svg(kind: str, active: bool) -> str:
    """Build one generic marker SVG without embedding intervention data."""

    safe_kind = kind if kind in MARKER_KINDS else "generic"
    color = _ACTIVE_COLORS[safe_kind] if active else _CLOSED_COLOR
    glyph = _GLYPHS[safe_kind]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
        f'<rect width="48" height="48" rx="24" fill="{color}"/>'
        f"{glyph}</svg>"
    )


class ArtemisMarkerView(HomeAssistantView):
    """Serve generic ARTEMIS marker SVGs."""

    url = f"{MARKER_URL_PREFIX}/{{status}}/{{kind}}.svg"
    name = "api:artemis:marker"
    requires_auth = False

    async def get(
        self,
        request: web.Request,
        status: str,
        kind: str,
    ) -> web.Response:
        """Return a marker image."""

        if status not in {"live", "closed"} or kind not in MARKER_KINDS:
            raise web.HTTPNotFound

        return web.Response(
            text=marker_svg(kind, status == "live"),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
