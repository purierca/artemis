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

EVENT_NEW_INTERVENTION = "artemis_new_intervention"

PLANNING_UPDATE_INTERVAL = timedelta(minutes=5)
OPERATIONS_UPDATE_INTERVAL = timedelta(seconds=15)
CENTER_COUNTER_UPDATE_INTERVAL = timedelta(seconds=30)
MAX_LOOKAHEAD_WEEKS = 8

UNAVAILABLE_STATUS_CODES = {"IND", "IN"}

# One-button personal availability cycle. Only these states are ever written.
STATUS_CYCLE = ("IND", "DI1", "AS1")
