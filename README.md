# Chicken Coop Door Controller (Raspberry Pi)

Opens and closes the chicken coop door on a schedule (fixed times, or
relative to sunrise/sunset), with a web UI for manual control and
configuration. Runs headless on a Raspberry Pi Zero W as a systemd
service.

This is a port of the NodeMCU/ESP8266 firmware in
[coopdoor](https://github.com/nickfish03/coopdoor). It moved to the Pi
because the NodeMCU's WiFi was too weak to stay reliably connected from
the coop. The web page, its routes and every safety behavior are the
same; only the platform underneath changed. It drives the Pi's
original relay wiring from
[chicken_door](https://github.com/nickfish03/chicken_door) unchanged,
and replaces that program.

There is no position feedback (no limit switches, no encoder). "Open"
and "close" just mean "energize a relay for N milliseconds, then stop."
Everything about the safety design (the confirm-before-repeat
interlock, the Stop button, the scheduler deferring to the last known
state) exists to work around that one fact.

---

## Part 1: Using it

### The web UI

Open `http://<pi's IP>/` in a browser on the same network (or
`http://<pi hostname>.local/` where mDNS resolves; see
[Troubleshooting](#troubleshooting)).

**Status (top of page)**
- Pi time: the Pi's current local time, and which server it's getting
  its time from (normally your router's address).
- A warning line appears under it if the Pi's clock hasn't synced from
  the internet since boot. While it shows, the scheduler is paused; the
  manual buttons still work. See [Clock sync](#clock-sync).
- Door: `idle`, `opening`, or `closing`, live.
- Last action: the last commanded action (`OPEN` or `CLOSED`), when it
  happened, and whether a manual click or the scheduler did it. The
  whole interlock is built on this record.

The footer shows which git commit is running and when the service
started, so you can confirm an update actually took.

**Manual Control**
- **Open Now / Close Now** trigger a move immediately, for the
  configured duration.
- **Stop** de-energizes both relays immediately. It is safe to click any
  time. Stopping mid-travel leaves the door's position uncertain, so it
  resets the "last action" record to `unknown`.

**Move Durations**: how long each relay stays energized, in
milliseconds (50 to 300000). This is the only "how far does the door
move" control. Defaults to 10000 ms (10 s), matching the old
chicken_door program's `motion = 10` for this hardware.

**Schedule**
- **Scheduler ON / OFF** is the master switch. OFF means fully manual.
- **Manual times**: fixed daily open/close times (HH:MM).
- **Sunrise/Sunset**: open and close relative to sunrise/sunset for
  Rhinelander, WI, each with an offset in minutes (positive = after,
  negative = before).
- Below that: today's sunrise/sunset, and **today's calculated
  open/close times**, which are exactly what the scheduler will act on.
- **Save** writes everything to disk, so it survives reboots and power
  loss. If a value is out of range, nothing is saved and the page says
  what was wrong.

**Fine Tuning** (bottom of the page)
- **Step (ms)**: how long one nudge runs the motor. Defaults to 500 ms
  each time the page loads; 50 to 5000 ms allowed.
- **▲ Up / ▼ Down**: run the motor up (the Open relay) or down (the
  Close relay) for one step, to adjust the door's height. **Stop**
  works here too.
- Nudges don't change the "last action" record, so they never trigger
  the confirm dialog and don't affect the scheduler. A door that was
  open is still "open" after a nudge.
- A nudge is refused while the door is already moving, so it can't
  interrupt a full open or close.
- **Net fine-tuning since last full open/close** totals your nudges
  (+ = up). It resets at every full move. Use it to tune the durations;
  see below.

### Tuning your durations

There's no feedback, so you have to measure how long the motor takes to
fully open and fully close the door, then add a safety margin
(typically 10-20%). The margin should be enough to reliably reach end
of travel, but not so much that the motor strains against a hard stop.
Time it with Open Now / Close Now and Stop, then set the duration and
Save.

Fine Tuning makes this easier. After a full Open, nudge the door up or
down until it sits where you want it, then read the net total. If it
says +1500 ms (up), the Open duration is about 1500 ms short. Add that
and Save. Do the same after a Close for the Close duration. The DC motor
runs slower in the cold, so expect to retune between seasons.

### The safety interlock (confirm-before-repeat)

Running the same action twice in a row (Open when the door is already
open) can drive the mechanism past its end of travel. So the
controller remembers the last thing it did and refuses to repeat it:

- **Open Now** when the last action was already `open` gets a
  confirmation dialog explaining why, with when and how that last
  action happened. OK re-sends with an explicit override; Cancel does
  nothing. Same for **Close Now** vs `closed`.
- The **scheduler** respects this silently: if you closed the door by
  hand at noon, the dusk close sees it's already closed and skips the
  move.
- **Stop** (mid-move) resets the record to `unknown`, and `unknown`
  never blocks the next move.

The record is saved to disk on every change, so it survives reboots.

### Updating

```
cd ~/git/pi_door
git pull
sudo systemctl restart coopdoor
```

If the pull changed anything in `deploy/` (the systemd units or
watchdog), run `sudo ./install.sh` instead of the restart. It's safe to
re-run any time. Restarting stops any in-progress move first.

### Logs and service control

```
journalctl -u coopdoor -f               # live controller log
journalctl -t coopdoor-watchdog         # WiFi watchdog log
sudo systemctl status coopdoor
sudo systemctl stop coopdoor            # relays forced off on stop
cat /var/lib/coopdoor/settings.json     # saved settings, human-readable
```

### Troubleshooting

- **`.local` name doesn't resolve.** mDNS doesn't cross VLAN boundaries,
  and some Windows setups don't resolve `.local` at all. Use the Pi's
  IP (`hostname -I` on the Pi). The Pi gets its address by DHCP, so a
  DHCP reservation on the router keeps it fixed.
- **"Clock not synced" warning won't go away.** The Pi has no battery
  clock and needs a time server once after each boot. Run
  `python3 -m coopdoor.ntpcheck` from `~/git/pi_door` to see whether the
  router answers, and `timedatectl timesync-status` to see what the
  Pi's time service is doing. See [Time from the router](#time-from-the-router).
- **Open and Close are backwards.** Swap `RELAY_OPEN_PIN` and
  `RELAY_CLOSE_PIN` in `coopdoor/config.py`, then restart.
- **Relay clicks the opposite of expected (on when it should be off).**
  The relay board is active-LOW; set `RELAY_ACTIVE_HIGH = False` in
  `coopdoor/config.py`.
- **Web page unreachable but the Pi is up.** Check `systemctl status
  coopdoor`. systemd restarts the controller automatically 5 s after
  any crash, so a persistent failure will show its error in `journalctl
  -u coopdoor`.

---

## Part 2: Setup and how the code works

### Hardware

- **Board:** Raspberry Pi Zero W, Raspberry Pi OS Bullseye (Python 3.9).
- **Relays:** same wiring as chicken_door. With the SD card slot facing
  up and at the top, rows are counted from the top:

  | Function                     | BCM | Physical pin | Old note |
  |------------------------------|-----|--------------|----------|
  | OPEN relay (DC+, clockwise)  | 17  | 11           | L6       |
  | CLOSE relay (DC-, ccw)       | 18  | 12           | R6       |
  | Relay board 3.3V             | -   | 17           | L9       |
  | GND                          | -   | 9            | L5       |

- **Relay logic:** active-HIGH (pin HIGH = relay energized), as the old
  program drove it. At power-on the Pi holds BCM 17/18 as inputs with
  pull-downs, so the relays stay off until the controller starts.

### First-time install

On the Pi, over SSH:

```
cd ~/git
git clone https://github.com/nickfish03/pi_door.git
cd pi_door
sudo ./install.sh
```

`install.sh`:
1. Installs `python3-flask` and `python3-rpi.gpio` from apt if they
   aren't already importable.
2. Sets the timezone to `America/Chicago` (from `config.py`) and makes
   sure NTP is on. Points the Pi's time service at the router first,
   then the internet, and checks that the router answers (see
   [Time from the router](#time-from-the-router)). Checks that the Pi takes its IP from DHCP. If
   `/etc/dhcpcd.conf` sets a static address, it prints the lines to
   remove but doesn't change them itself, because that would drop your
   SSH session mid-install. Set the reservation on the router first,
   then remove the static lines and reboot.
3. Retires chicken_door. It stops the running program and comments out
   its `@reboot` line and the old `ping 8.8.8.8 || reboot` line in your
   and root's crontabs. A backup is saved in your home directory first.
   Both programs drive the same pins and port, so they must not run
   together.
4. Installs and starts `coopdoor.service` and the WiFi watchdog timer,
   then prints the URL.

The service runs as your normal user (not root). It gets GPIO access
from the `gpio` group, and permission to use port 80 from a single
systemd capability.

### Trying it without the Pi

On any machine with Python 3 and Flask:

```
python3 -m coopdoor --fake-gpio --port 8080      # then open http://localhost:8080
python3 -m unittest discover -s tests -t .      # 51 tests, no hardware needed
```

`--fake-gpio` logs relay changes instead of touching pins, and treats
the clock as synced. A by-hand run keeps its settings in `./state/`
(gitignored), never the real `/var/lib/coopdoor`.

### Project layout

```
install.sh                  One-shot setup/update script (run with sudo)
coopdoor/
  config.py                 Site settings: pins, relay logic, location,
                            limits, port. Edit before installing.
  relays.py                 The only GPIO code (real RPi.GPIO + fake)
  relays_off.py             "Force both relays off" helper for systemd
  door.py                   Timed relay moves (DoorController)
  suntimes.py               Sunrise/sunset (NOAA algorithm)
  settings.py               Persisted settings (JSON, atomic saves)
  clock.py                  "Has NTP synced since boot?" + current time source
  ntpcheck.py               "Will this server give the Pi its time?" checker
  controller.py             Interlock, scheduler, status
  web.py                    Flask routes
  __main__.py               Entry point (python3 -m coopdoor)
  static/index.html         The whole web UI
deploy/
  coopdoor.service          systemd unit for the controller
  coopdoor-watchdog.*       systemd timer + oneshot for the WiFi watchdog
  net-watchdog.sh           The watchdog itself
tests/                      unittest suite (fake GPIO, injected clock)
```

### How the code works

#### NodeMCU → Pi, piece by piece

| NodeMCU (coopdoor)                   | Pi (this project)                                     |
|--------------------------------------|-------------------------------------------------------|
| `DoorController` + `millis()`        | `door.py`: `threading.Timer` + `monotonic()` backstop |
| `checkScheduler()` in `loop()`       | Background thread, `scheduler_tick()` once a second   |
| EEPROM struct + `SETTINGS_MAGIC`     | JSON file, per-field validation, atomic save          |
| Dusk2Dawn library                    | `suntimes.py`, same NOAA algorithm, no dependency     |
| `configTzTime` + POSIX TZ            | Pi's system timezone + systemd-timesyncd              |
| "time > 1970" sync check             | `clock.py`: asks the OS if NTP synced since boot      |
| `ESP8266WebServer`                   | Flask (same routes, same JSON keys)                   |
| `WebPage.h` PROGMEM string           | `static/index.html` (same page)                       |
| ArduinoOTA                           | `git pull` + `systemctl restart`                      |
| Non-blocking `maintainWiFi()`        | The OS handles WiFi; `net-watchdog.sh` as a backstop  |
| Daily 3 AM reboot (heap)             | Not needed; systemd restarts on crash                 |
| "Firmware built" footer              | git commit + service start time                       |

#### Turning relays off: four layers

The controller has no position feedback, so a relay left on is the main
thing that can do damage. Each layer below is enough on its own:

1. **Timer.** Every move starts a `threading.Timer` for exactly its
   duration. Each move gets an ID, so a stale timer from an earlier,
   interrupted move can't cut a newer one short.
2. **Backstop.** The scheduler thread calls `door.update()` every
   second, which stops any move past its deadline. Deadlines use
   `time.monotonic()`, so an NTP clock correction can't stretch a move.
3. **Clean shutdown.** SIGTERM (from `systemctl stop/restart`) is
   turned into a normal exit, which stops the door.
4. **systemd.** `ExecStopPost` runs `relays_off` after the controller
   exits for any reason, including a crash or `kill -9`. A GPIO pin
   otherwise keeps its last level after its process dies.

It also never calls `GPIO.cleanup()`, which would turn the pins into
floating inputs. Pins are left as outputs driven to "off."

**Reversing.** When a move is triggered while the opposite one is still
running, both relays go off for 0.25 s before the other energizes
(`RELAY_REVERSE_DEADTIME_S`). This is new compared with the NodeMCU,
which switched instantly. It keeps the two relays' contacts from
overlapping and is easier on the motor.

#### The scheduler

Same logic as the NodeMCU's `checkScheduler()`. Once a second it
compares the current minute with today's targets, fires each event at
most once per calendar day, and applies the interlock silently.
`todays_targets()` is shared with `/status`, so the UI's "calculated
open/close" is exactly what the scheduler uses. Saving settings clears
the day's latches, so a new time later today still fires.

As on the NodeMCU, an event fires only if the controller is running
during that exact minute. A Pi that's rebooting or powered off at 07:31
does not catch up afterwards.

#### Clock sync

The ESP8266 booted at 1970 until NTP synced, so a "year > 1970" check
was enough. A Pi restores the time it saved at last shutdown
(fake-hwclock). That time looks plausible but can be hours or days
stale after a power outage. So `clock.py` asks the OS whether NTP has
actually synced since this boot, using systemd-timesyncd's
`/run/systemd/timesync/synchronized` flag with `timedatectl` as a
fallback. Until it has, the scheduler doesn't act and the UI shows a
warning. Once synced, the result is latched for the rest of the boot,
in `/run/coopdoor`, so a service restart doesn't lose it either. The Pi
then keeps good time through a WiFi outage, so the scheduler keeps
working, just as the ESP did.

One case needs extra help: when the WiFi watchdog reboots the Pi, the
outage is still going, so there's no NTP to re-sync from. If the clock
was synced, the watchdog writes a timestamp to
`/var/lib/coopdoor/clock-trusted-reboot` just before rebooting. A clean
reboot saves and restores the time, so it's off by about the length of
the reboot. On the next start, `clock.py` trusts that restored clock
if the marker is under 15 minutes old and the Pi booted under 15
minutes ago. It deletes the marker either way, so the marker can't
vouch for a clock restored after an unplanned power cut. Without this,
a watchdog reboot would pause the scheduler for the rest of the
outage.

The remaining gap is a real power outage while the internet is also
down. The Pi then has no way to know the time until it can reach an
NTP server.

#### Time from the router

The Pi asks the **router** for the time first and the internet pool
servers second. The order comes from `NTP_SERVERS` in `config.py`, and
`install.sh` writes it to
`/etc/systemd/timesyncd.conf.d/coopdoor.conf`. `"gateway"` in that list
means the Pi's default router, looked up at install time; re-run
`install.sh` if the router's address changes.

**What this covers.** A Pi that's already running doesn't need a time
server at all. Once synced, it keeps time on its own and the scheduler
keeps running through any outage. A time server is only needed after
the Pi restarts, from a power blip or a watchdog reboot. With the router
as the source, the Pi can set its clock after a restart even when the
internet is down, as long as the router stayed up.

**What it doesn't cover.** The EdgeRouter X has no battery clock either.
If the power goes out at the house and the internet is also down when
it comes back, the router doesn't know the time. It reports itself as
unsynchronized, and the Pi correctly refuses to trust it. Until the
internet is back the scheduler waits, and the manual buttons still
work. Two ways to close that gap:
- Put the Synology's IP first in `NTP_SERVERS` and turn on its time
  server (DSM: Control Panel → Regional Options → Time → NTP service).
  The NAS has a battery clock, so it knows the time after a power cut.
  Whether it reports itself as synced with no internet is something to
  test with the check below.
- Add a battery clock (RTC) module to the Pi itself. That's the only
  option that doesn't depend on anything else being up.

**Checking it.** `install.sh` runs this automatically. To run it by
hand:

```
cd ~/git/pi_door
python3 -m coopdoor.ntpcheck          # the router
python3 -m coopdoor.ntpcheck 192.168.x.x   # any other server
```

It sends one time request and applies the Pi's own acceptance rules:
the server answers, isn't flagged unsynchronized, has a stratum of
1-15, and has a root distance under 5 s. The result is "Usable", "NOT
usable" with the reason, or "NO ANSWER".

- **NO ANSWER** usually means a firewall rule. If the coop network has
  a LOCAL firewall ruleset on the EdgeRouter that only allows DNS and
  DHCP to the router, add a rule accepting **UDP port 123** to the
  router. EdgeOS normally answers time requests without any other
  setup.
- **Testing the outage case**: unplug the router's WAN (internet)
  cable for 15-30 minutes and run the check again. If it still says
  "Usable", the Pi can set its clock from the router through an
  internet outage. If it says "NOT usable" (unsynchronized), this
  router stops vouching for its own time once it loses its upstream.
  The Pi would then fall back to waiting for the internet after a
  restart, and the Synology or an RTC module is the answer.

#### Sunrise/sunset

`suntimes.py` implements NOAA's solar calculator: the same algorithm
family as the Dusk2Dawn library the NodeMCU used, written out so no
extra package is needed. It computes in UTC and converts with the Pi's
timezone database, so DST needs no special handling. Results are
checked against the `astral` library in `tests/test_suntimes.py`
(within a minute, across DST changes and both solstices).

#### Settings

`/var/lib/coopdoor/settings.json`, readable with `cat`. On load, each
field is validated on its own. A missing or bad field falls back to
its default, and a corrupt file means all defaults. Unlike the EEPROM
struct, adding fields later never resets your other settings, so there
is no magic number to bump. Saves write a temp file, `fsync` it, and
rename it over the old one, so a power cut leaves either the old file
or the new one.

`lastActionAt` and `lastActionSource` are now persisted too (the ESP
kept them in RAM), so the UI still says when and how the door last
moved after a reboot.

#### Web routes

Identical to the NodeMCU's:

| Route     | Method | Purpose                                            |
|-----------|--------|----------------------------------------------------|
| `/`       | GET    | The single-page UI                                 |
| `/status` | GET    | JSON snapshot the page polls every 5 s             |
| `/open`   | POST   | Open (409 + JSON if repeating; `?confirm=1` overrides) |
| `/close`  | POST   | Close (same interlock)                             |
| `/stop`   | POST   | Stop immediately                                   |
| `/save`   | POST   | Save settings (400 + reason if a value is invalid) |
| `/jog`    | POST   | Fine-tune nudge: `dir=up\|down`, `ms=50..5000` (new; 409 if moving) |

`/status` gained `clockSynced`, `timeSource`, `jogNetMs`, and `version` (which
replaces `firmwareBuilt`). The only other behavior change is that `/save` is now
all-or-nothing and reports what it rejected. The ESP silently ignored
bad fields.

The server is Flask's built-in one. That's fine for a single household
on a LAN, and it's what chicken_door already used.

#### WiFi watchdog

`deploy/net-watchdog.sh` runs once a minute (systemd timer, as root).
It replaces the old crontab line that rebooted whenever `8.8.8.8`
didn't answer. That line rebooted whenever the internet was down, even
with WiFi fine, and could reboot mid-move. The new watchdog:

- turns off WiFi power-saving on every run (a well-known cause of Pi
  Zero W dropouts);
- pings the local router, not the internet;
- after 5 failed minutes in a row, asks wpa_supplicant to reconnect;
  after 7, bounces `wlan0`; after 10, reboots;
- never reboots while the door is moving, and never more than once
  per 30 minutes of uptime, so a dead router can't cause a reboot loop;
- leaves a trusted-clock marker before rebooting (see
  [Clock sync](#clock-sync)).

### Extending this project

- **New `/status` field** → add a `d.<field>` read in
  `static/index.html`. `tests/test_web.py` fails if the page reads a
  field `/status` doesn't send.
- **New setting** → add it to `DEFAULTS` and `_valid()` in
  `settings.py`. Existing saved settings are kept.
- **Limit switches.** The real fix for "no position feedback" would be
  switches at each end of travel on spare GPIO pins. `door.py` and the
  single `lastAction` concept keep that a contained change.
- **No authentication.** Anyone on the network can open or close the
  door. That's fine on a trusted home network.
