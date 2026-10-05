# ============================================================================
# config.py - site-specific settings you edit before deploying
#
# Everything here is fixed at install time. Things you change from the web
# UI (durations, schedule, offsets) live in the settings file instead - see
# settings.py. There are no secrets in this project any more: WiFi is the
# Pi's own job (configured in the OS), and OTA was replaced by `git pull`.
# ============================================================================

import os

# ---------------------------------------------------------------------------
# Relay pins (BCM numbering - the "GPIO17" names, not physical pin numbers)
# ---------------------------------------------------------------------------
# These match the original chicken_door wiring exactly, so nothing has to be
# re-wired at the coop:
#
#   BCM 17 = physical pin 11 ("L6" in the old notes: SD slot up, at the top,
#            6th pin down on the left column) -> relay for DC+ / clockwise
#            = OPEN
#   BCM 18 = physical pin 12 ("R6") -> relay for DC- / counter-clockwise
#            = CLOSE
#   Relay board power/ground from the old notes: 3.3V at "L9" = physical
#   pin 17, GND at "L5" = physical pin 9.
#
# If Open and Close come out backwards, swap these two numbers rather than
# the wires.
RELAY_OPEN_PIN = 17
RELAY_CLOSE_PIN = 18

# The old chicken_door program drove the pins HIGH to energize a relay, so
# these relays are ACTIVE-HIGH. (The NodeMCU's relay board was active-LOW;
# if you ever swap to a board like that, set this to False.)
#
# Active-HIGH is also the safe choice for boot: the Pi powers up with
# BCM 17/18 as inputs with their internal pull-DOWN on, so the relays stay
# off from power-on until this program takes over the pins.
RELAY_ACTIVE_HIGH = True

# When a move is triggered while the opposite direction is still running
# (e.g. Close clicked mid-Open), wait this long with both relays off before
# energizing the other one. Mechanical relays take a few milliseconds to
# release, and reversing a DC motor's polarity instantly is hard on it and
# risks both relays' contacts briefly overlapping. 0.25 s is imperceptible
# in use. The NodeMCU version switched instantly; this is a deliberate
# safety addition.
RELAY_REVERSE_DEADTIME_S = 0.25

# ---------------------------------------------------------------------------
# Location - Rhinelander, Wisconsin (hardcoded, no GPS)
# ---------------------------------------------------------------------------
LOCATION_LAT = 45.6363
LOCATION_LON = -89.4126

# The timezone the Pi's system clock should be in. All schedule math uses
# the Pi's local time, which handles DST automatically. install.sh sets the
# Pi to this zone if it isn't already; this value isn't read at runtime.
TIMEZONE = "America/Chicago"

# ---------------------------------------------------------------------------
# Durations - sanity limits for what /save accepts
# ---------------------------------------------------------------------------
MIN_DURATION_MS = 50
MAX_DURATION_MS = 300000   # 5 min. Raise if your actuator genuinely needs longer.

# Sun offsets are limited to +/- 12 hours; anything bigger isn't a sane
# "minutes before/after sunrise" value.
MAX_SUN_OFFSET_MIN = 720

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
HTTP_HOST = "0.0.0.0"
# Port 80 normally needs root. The systemd unit grants just the one
# capability needed for it (CAP_NET_BIND_SERVICE), so the program itself
# runs as your normal user. Overridable with --port for testing.
HTTP_PORT = int(os.environ.get("COOPDOOR_PORT", "80"))

# ---------------------------------------------------------------------------
# Where settings are persisted
# ---------------------------------------------------------------------------
# The systemd unit sets COOPDOOR_STATE_DIR=/var/lib/coopdoor (created and
# owned for us by systemd's StateDirectory=). When run by hand without it,
# settings go in ./state next to the repo so a test run never touches the
# real settings.
STATE_DIR = os.environ.get(
    "COOPDOOR_STATE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state"),
)
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
# How often the background thread wakes up to check the schedule and the
# relay-timeout backstop. Events are matched to the minute, so anything well
# under 60 s works; 1 s keeps the backstop tight.
SCHEDULER_TICK_S = 1.0
