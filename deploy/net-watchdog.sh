#!/bin/bash
# ============================================================================
# net-watchdog.sh - keep the Pi's WiFi alive, safely
#
# Replaces the old crontab line that rebooted whenever 8.8.8.8 didn't
# answer a ping. That one had two problems: it rebooted whenever your
# INTERNET was down (even with the coop WiFi fine), and it could reboot in
# the middle of a door move. This one:
#
#   - turns off WiFi power-saving every run (a well-known cause of Pi Zero W
#     dropouts; cheap to repeat, and survives the driver resetting it)
#   - checks the local router (default gateway) instead of the internet
#   - escalates gently, one step per minute of continuous failure:
#        5 min  -> ask wpa_supplicant to reconnect
#        7 min  -> bounce the WiFi interface
#       10 min  -> reboot, but ONLY if the door isn't moving, and never
#                  more often than once per MIN_UPTIME_S of uptime (so a
#                  dead router means one reboot every 30 min, not a loop)
#
# Before rebooting, if the clock is NTP-synced it leaves a marker so the
# controller trusts the restored clock after the reboot even though there's
# still no network to re-sync from (see coopdoor/clock.py). Otherwise the
# scheduler would sit paused for the rest of the outage.
#
# Run once a minute as root by coopdoor-watchdog.timer. Logs to the journal:
#   journalctl -t coopdoor-watchdog
# ============================================================================

set -u

IFACE="${COOPDOOR_WIFI_IFACE:-wlan0}"
STATE_DIR=/run/coopdoor-watchdog          # tmpfs: counter resets every boot
RECONNECT_AFTER=5
BOUNCE_AFTER=7
REBOOT_AFTER=10
MIN_UPTIME_S=1800
STATUS_URL="http://127.0.0.1/status"
CLOCK_MARKER=/var/lib/coopdoor/clock-trusted-reboot

log() { logger -t coopdoor-watchdog "$*"; }

mkdir -p "$STATE_DIR"
FAILS_FILE="$STATE_DIR/fails"

# 1. Power save off (no-op if already off, or if there's no such interface).
iw dev "$IFACE" set power_save off 2>/dev/null || true

# 2. Can we reach the router?
gw=$(ip -4 route show default dev "$IFACE" 2>/dev/null | awk '{print $3; exit}')
if [ -n "$gw" ] && ping -q -c 2 -W 3 "$gw" >/dev/null 2>&1; then
    prev=$(cat "$FAILS_FILE" 2>/dev/null || echo 0)
    if [ "$prev" -gt 0 ] 2>/dev/null; then
        log "WiFi OK again (router $gw reachable) after $prev failed minute(s)"
    fi
    echo 0 > "$FAILS_FILE"
    exit 0
fi

fails=$(( $(cat "$FAILS_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$FAILS_FILE"
log "Router ${gw:-(no default route)} unreachable on $IFACE for $fails minute(s)"

# 3. Escalate.
if [ "$fails" -eq "$RECONNECT_AFTER" ]; then
    log "Asking wpa_supplicant to reconnect"
    wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || true
elif [ "$fails" -eq "$BOUNCE_AFTER" ]; then
    log "Bouncing $IFACE"
    ip link set "$IFACE" down; sleep 3; ip link set "$IFACE" up
elif [ "$fails" -ge "$REBOOT_AFTER" ]; then
    uptime_s=$(cut -d. -f1 /proc/uptime)
    if [ "$uptime_s" -lt "$MIN_UPTIME_S" ]; then
        log "Would reboot, but uptime is only ${uptime_s}s (< ${MIN_UPTIME_S}s) - waiting"
        exit 0
    fi
    # Never reboot mid-move. If the controller doesn't answer at all, it
    # isn't timing a move either (and systemd already forced the relays
    # off if it died), so a reboot is fine.
    door=$(python3 -c '
import json, sys, urllib.request
try:
    print(json.load(urllib.request.urlopen(sys.argv[1], timeout=3)).get("doorState", ""))
except Exception:
    print("")
' "$STATUS_URL")
    if [ "$door" = "opening" ] || [ "$door" = "closing" ]; then
        log "Would reboot, but the door is $door - retrying next minute"
        exit 0
    fi
    # /run/coopdoor/clock-synced also counts: it's the controller's own
    # latch, and covers a second watchdog reboot in the same long outage
    # (each one costs the clock roughly a reboot's worth of drift).
    if [ -e /run/systemd/timesync/synchronized ] || [ -e /run/coopdoor/clock-synced ] \
       || [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
        date +%s > "$CLOCK_MARKER" && sync
    fi
    log "Rebooting to recover WiFi"
    systemctl reboot
fi
exit 0
