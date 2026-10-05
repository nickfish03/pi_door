#!/bin/bash
# ============================================================================
# install.sh - set up (or update) the coop door controller on the Pi
#
#   cd ~/git/pi_door && sudo ./install.sh
#
# Safe to re-run any time (e.g. after a `git pull` that changed something
# in deploy/). It:
#   1. installs the two OS packages it needs (Flask, RPi.GPIO) from apt
#   2. sets the Pi's timezone to the one in coopdoor/config.py
#   3. retires the old chicken_door program: stops it, and comments out its
#      crontab lines (after saving a backup) so it can't start again and
#      fight over the same GPIO pins / port 80
#   4. installs + starts the systemd service and the WiFi watchdog timer
# ============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo:  sudo ./install.sh" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-pi}"
if [ "$RUN_USER" = "root" ]; then
    echo "Run this as your normal user with sudo (not from a root shell), so the" >&2
    echo "service runs as that user. Or: sudo SUDO_USER=pi ./install.sh" >&2
    exit 1
fi
echo "==> Repo: $REPO"
echo "==> Service will run as: $RUN_USER"

# --- 1. packages -----------------------------------------------------------
if python3 -c 'import flask, RPi.GPIO' 2>/dev/null; then
    echo "==> Flask and RPi.GPIO already installed"
else
    echo "==> Installing packages (python3-flask, python3-rpi.gpio)"
    apt-get install -y -q python3-flask python3-rpi.gpio >/dev/null \
        || { apt-get update -q >/dev/null && apt-get install -y -q python3-flask python3-rpi.gpio >/dev/null; }
fi
usermod -a -G gpio "$RUN_USER"

# --- 2. timezone -----------------------------------------------------------
TZ_WANTED=$(cd "$REPO" && python3 -c 'from coopdoor import config; print(config.TIMEZONE)')
TZ_NOW=$(timedatectl show -p Timezone --value 2>/dev/null || echo "")
if [ "$TZ_NOW" != "$TZ_WANTED" ]; then
    echo "==> Setting timezone $TZ_NOW -> $TZ_WANTED"
    timedatectl set-timezone "$TZ_WANTED"
else
    echo "==> Timezone already $TZ_WANTED"
fi
# Make sure NTP is on - the scheduler waits for a synced clock.
timedatectl set-ntp true || true

# --- 2a. time servers: the router first ------------------------------------
# So the Pi can set its clock during an internet outage, as long as the
# router is up. Order comes from NTP_SERVERS in coopdoor/config.py;
# "gateway" = this Pi's default router, looked up now.
GW=$(cd "$REPO" && python3 -c 'from coopdoor.ntpcheck import default_gateway as g; print(g() or "")')
NTP_LIST=$(cd "$REPO" && python3 -c 'from coopdoor import config; print(" ".join(config.NTP_SERVERS))')
servers=""
for s in $NTP_LIST; do
    if [ "$s" = "gateway" ]; then
        if [ -n "$GW" ]; then
            servers="$servers $GW"
        else
            echo "!!  Couldn't find the router's address (no default route) - leaving it out"
        fi
    else
        servers="$servers $s"
    fi
done
servers="${servers# }"
mkdir -p /etc/systemd/timesyncd.conf.d
{
    echo "# Written by pi_door install.sh from NTP_SERVERS in coopdoor/config.py."
    echo "# Edit that list and re-run install.sh rather than editing this file."
    echo "[Time]"
    echo "NTP=$servers"
} > /etc/systemd/timesyncd.conf.d/coopdoor.conf
echo "==> Time servers, in order: $servers"
if systemctl is-enabled --quiet systemd-timesyncd 2>/dev/null; then
    systemctl restart systemd-timesyncd
else
    echo "!!  systemd-timesyncd isn't the time service on this Pi (is the 'ntp' or"
    echo "    'chrony' package installed?), so the list above won't be used."
    echo "    'sudo apt remove ntp chrony' hands time back to systemd-timesyncd."
fi
if [ -n "$GW" ]; then
    echo "==> Checking that the router ($GW) answers time requests:"
    if ! (cd "$REPO" && python3 -m coopdoor.ntpcheck "$GW") | sed 's/^/    /'; then
        echo "    (Until that's fixed the Pi uses the internet servers - see README 'Time from the router'.)"
    fi
fi

# --- 2b. IP address: should come from DHCP ---------------------------------
# The controller doesn't care what IP the Pi has; give it a fixed address
# with a DHCP reservation on the router. A static address set on the Pi
# itself (Bullseye: /etc/dhcpcd.conf) would override that, so warn about
# one - but never edit network config from here: changing it mid-install
# would cut off the SSH session running this script.
if grep -Eq '^[[:space:]]*static[[:space:]]+ip_address' /etc/dhcpcd.conf 2>/dev/null; then
    echo
    echo "!!  /etc/dhcpcd.conf sets a static IP on this Pi:"
    grep -En '^[[:space:]]*(interface|static)' /etc/dhcpcd.conf | sed 's/^/      /'
    echo "    To use DHCP (with a reservation on your router) instead, comment out"
    echo "    those 'interface'/'static' lines (sudo nano /etc/dhcpcd.conf), then"
    echo "    sudo reboot. Do it AFTER setting up the router reservation, and expect"
    echo "    your SSH session to drop - reconnect at the reserved address."
    echo
else
    echo "==> IP comes from DHCP (no static address in /etc/dhcpcd.conf)"
fi

# --- 3. retire chicken_door -------------------------------------------------
retire_crontab() {   # $1 = user whose crontab to clean
    local u="$1" current backup
    current=$(crontab -u "$u" -l 2>/dev/null || true)
    if grep -Eq '^[^#].*(chick_simple|chick_scheduled|chicken_door|8\.8\.8\.8.*reboot)' <<<"$current"; then
        backup="/home/$RUN_USER/crontab-$u-backup-$(date +%Y%m%d-%H%M%S).txt"
        echo "$current" > "$backup"
        chown "$RUN_USER": "$backup"
        sed -E 's@^([^#].*(chick_simple|chick_scheduled|chicken_door|8\.8\.8\.8.*reboot).*)$@# disabled by pi_door install.sh: \1@' <<<"$current" \
            | crontab -u "$u" -
        echo "==> Disabled old chicken_door / ping-reboot lines in $u's crontab (backup: $backup)"
    fi
}
retire_crontab "$RUN_USER"
retire_crontab root
if pkill -f 'chick_(simple|scheduled)\.py' 2>/dev/null; then
    echo "==> Stopped the running chicken_door program"
    sleep 1
fi

# --- 4. systemd units -------------------------------------------------------
echo "==> Installing systemd units"
for unit in coopdoor.service coopdoor-watchdog.service coopdoor-watchdog.timer; do
    sed -e "s|@USER@|$RUN_USER|g" -e "s|@REPO@|$REPO|g" \
        "$REPO/deploy/$unit" > "/etc/systemd/system/$unit"
done
chmod +x "$REPO/deploy/net-watchdog.sh" "$REPO/install.sh" 2>/dev/null || true
systemctl daemon-reload
systemctl enable coopdoor.service coopdoor-watchdog.timer >/dev/null
systemctl restart coopdoor.service
systemctl restart coopdoor-watchdog.timer

sleep 3
echo
systemctl --no-pager --lines=8 status coopdoor.service || true
echo
IP=$(hostname -I | awk '{print $1}')
echo "Done. Web UI: http://$IP/   (or http://$(hostname).local/ where mDNS works)"
echo "Logs:   journalctl -u coopdoor -f"
echo "Update: git pull && sudo systemctl restart coopdoor   (re-run install.sh if deploy/ changed)"
