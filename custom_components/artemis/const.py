"""Constants for the ARTEMIS integration."""

from datetime import timedelta

DOMAIN = "artemis"

BACKEND_WEBEVO = "webevo"
BACKEND_SMARTEMIS = "smartemis"
CONF_BACKEND = "backend"

CONF_HOST = "host"
CONF_CAS_SERVICE = "cas_service"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

CONF_SMARTEMIS_SERVER = "smartemis_server"
CONF_SMARTEMIS_PHONE_ID = "smartemis_phone_id"

DEFAULT_HOST = "https://artemisweb.sdis39.fr"
DEFAULT_CAS_SERVICE = "https://artemis/artemis-web"

PLANNING_UPDATE_INTERVAL = timedelta(minutes=5)
CENTER_COUNTER_UPDATE_INTERVAL = timedelta(seconds=30)
SMARTEMIS_UPDATE_INTERVAL = timedelta(seconds=30)
MAX_LOOKAHEAD_WEEKS = 8

# Public WebEvo write cycle. Smartemis writes remain internal until live validation.
STATUS_CYCLE = ("IND", "DI1", "AS1")
