"""Constants for the Safera Sense integration."""

DOMAIN = "safera_sense"

# Options
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 30  # seconds
MIN_UPDATE_INTERVAL = 1
MAX_UPDATE_INTERVAL = 300

# Raw SENSOR_REPORT offsets not parsed by safera-sense-ble.
REPORT_MOTOR1 = 57  # damper on an ES (central-ventilation) hood
REPORT_MOTOR2 = 58  # roof fan on an ES hood

# Motor 2 output the hood itself leaves when off or at level 1. Anything
# above this with the fan step at 0 is a raw command, i.e. our bathroom
# ventilation.
ROOF_FAN_IDLE_MAX = 13

# Motor 2 output for bathroom ventilation: what the hood's own bathroom
# mode uses (the installer's level-4 preset on this hood).
BATHROOM_ROOF_FAN_SPEED = 127

# Seconds after an HA fan-step command during which a 0 -> 120 jump is
# attributed to HA rather than to the hood's bathroom mode.
STEP_COMMAND_GRACE = 5.0

# Ventilation modes.
MODE_OFF = "off"
MODE_KITCHEN = "kitchen"
MODE_BATHROOM_HOOD = "bathroom_hood"
MODE_BATHROOM_HA = "bathroom_ha"
VENTILATION_MODES = [MODE_OFF, MODE_KITCHEN, MODE_BATHROOM_HOOD, MODE_BATHROOM_HA]
