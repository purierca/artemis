"""Constants for the ARTEMIS WebEvo integration."""

from datetime import timedelta

DOMAIN = "artemis"
NAME = "ARTEMIS WebEvo"

DEFAULT_HOST = "https://artemisweb.sdis39.fr"
DEFAULT_CAS_SERVICE = "https://artemis/artemis-web"

CONF_HOST = "host"
CONF_CAS_SERVICE = "cas_service"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Personal planning is comparatively static, so poll it slowly and schedule an
# exact refresh on the next known boundary.
PLANNING_UPDATE_INTERVAL = timedelta(minutes=5)

# WebEvo exposes its own operations refresh interval. This is only the fallback.
OPERATIONS_UPDATE_INTERVAL = timedelta(seconds=15)

# The native centre counter is useful in the compact status sensor, but does not
# need to be fetched as often as the live operations synoptic.
CENTER_COUNTER_UPDATE_INTERVAL = timedelta(seconds=30)

MAX_LOOKAHEAD_WEEKS = 8

# One-button personal availability cycle. Only these states are ever written.
STATUS_CYCLE = ("IND", "DI1", "AS1")
