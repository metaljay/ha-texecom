"""Constants for the Texecom integration."""

DOMAIN = "texecom"
EVENT = "texecom_event"

CONF_PROTOCOL = "protocol"
PROTOCOL_CONNECT = "connect"
PROTOCOL_CRESTRON = "crestron"

CONF_UDL = "udl"
CONF_CONNECTION = "connection"
CONNECTION_NETWORK = "network"
CONNECTION_SERIAL = "serial"
CONF_SERIAL_DEVICE = "serial_device"
CONF_BAUD_RATE = "baud_rate"
CONF_ZONE_COUNT = "zone_count"
CONF_AREA_COUNT = "area_count"

# Stored from discovery (Connect).
CONF_INFO = "info"
CONF_ZONES = "zones"
CONF_AREAS = "areas"

# Options.
CONF_HOME_PART_ARM = "home_part_arm"
CONF_NIGHT_PART_ARM = "night_part_arm"
CONF_KEYPAD_ARM_MODE = "keypad_arm_mode"
CONF_ALARM_CODE = "alarm_code"
CONF_CODE_ARM_REQUIRED = "code_arm_required"
CONF_TIME_SYNC = "time_sync"
CONF_STATUS_POLL = "status_poll"
CONF_REDISCOVER = "rediscover"
CONF_CREATE_DASHBOARD = "create_dashboard"

DEFAULT_CONNECT_PORT = 10001
DEFAULT_CRESTRON_PORT = 23
DEFAULT_BAUD_RATE = 19200
DEFAULT_UDL = "1234"
DEFAULT_STATUS_POLL = 60
TIME_SYNC_HOURS = 24
CLOCK_DRIFT_LIMIT = 300  # seconds; more than this raises a Repairs notice (clock sync off)

# More options.
CONF_USER_NAMES = "user_names"  # {"3": "Sam"}: names for keypad users

# Options a running panel driver depends on: changing one reconnects to the
# panel. Others (codes, names, notifications) apply straight away.
DRIVER_OPTIONS = (CONF_HOME_PART_ARM, CONF_NIGHT_PART_ARM, CONF_KEYPAD_ARM_MODE, CONF_TIME_SYNC, CONF_STATUS_POLL)

# Help pages linked from the screens (links can't be written into strings.json).
DOCS_URL = "https://github.com/metaljay/ha-texecom/blob/main/docs/user/"
HELP_SETUP = DOCS_URL + "setup.md"
HELP_PART_ARMS = DOCS_URL + "setup.md#part-arms-explained"
HELP_CRESTRON = DOCS_URL + "crestron.md"
HELP_OPTIONS = DOCS_URL + "using.md#options"
HELP_DASHBOARD = DOCS_URL + "using.md#build-the-dashboard-yourself"
HELP_OFFLINE = DOCS_URL + "troubleshooting.md#the-panel-is-unreachable"
