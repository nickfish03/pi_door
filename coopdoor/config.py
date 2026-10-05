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
# Time servers (NTP)
# ---------------------------------------------------------------------------
# Where the Pi gets its time, in order of preference. install.sh writes these
# into systemd-timesyncd's config; the Pi uses the first one that answers
# with a synced clock and moves down the list if it can't.
#
# "gateway" means the Pi's default router (the EdgeRouter's address on the
# coop's network), looked up when install.sh runs - re-run install.sh if
# the router's address ever changes. Asking the router first means the Pi
# can still set its clock during an INTERNET outage, as long as the router
# itself kept running (see README "Clock sync" for the limits of that).
# The internet pool servers are the backup for when the router doesn't
# answer.
#
# First choice is the Synology NAS (192.168.1.5, static): it has a battery
# clock, so it still knows the time after a power cut even if the internet
# is down too - which the EdgeRouter X (no battery clock) can't. The
# router is the backup, then the internet. Requires DSM's NTP service to
# be on: Control Panel > Regional Options > NTP Service.
NTP_SERVERS = ["192.168.1.5", "gateway", "0.debian.pool.ntp.org", "1.debian.pool.ntp.org"]

# ---------------------------------------------------------------------------
# Durations - sanity limits for what /save accepts
# ---------------------------------------------------------------------------
MIN_DURATION_MS = 50
MAX_DURATION_MS = 300000   # 5 min. Raise if your actuator genuinely needs longer.

# Fine-tuning (Up/Down nudge) step limits. The page's box defaults to 500 ms.
# The max is deliberately well below a full move: a nudge is for adjusting
# the door's height, not for opening or closing it.
JOG_DEFAULT_MS = 500
JOG_MIN_MS = 50
JOG_MAX_MS = 5000

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

# Written by deploy/net-watchdog.sh just before it reboots the Pi, when the
# clock was NTP-synced at that moment. On the next boot clock.py accepts it
# (once) as proof the restored time is good - see clock.py for why. The
# watchdog has this path hardcoded as /var/lib/coopdoor/clock-trusted-reboot.
CLOCK_REBOOT_MARKER = os.path.join(STATE_DIR, "clock-trusted-reboot")

# "Clock has synced since this boot" flag, so a service restart mid-outage
# doesn't forget it. Must be on a tmpfs that's cleared every boot: the
# systemd unit sets /run/coopdoor (RuntimeDirectory=, preserved across
# service restarts). Not set for by-hand runs, which then just don't keep
# the flag across restarts.
RUNTIME_DIR = os.environ.get("COOPDOOR_RUNTIME_DIR")
CLOCK_SYNCED_FLAG = os.path.join(RUNTIME_DIR, "clock-synced") if RUNTIME_DIR else None

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
# How often the background thread wakes up to check the schedule and the
# relay-timeout backstop. Events are matched to the minute, so anything well
# under 60 s works; 1 s keeps the backstop tight.
SCHEDULER_TICK_S = 1.0
