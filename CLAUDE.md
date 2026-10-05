# CLAUDE.md

Project memory for future sessions. `README.md` is the full user guide
and code walkthrough; this file covers what to know before touching
anything, and how this project tends to get edited.

## What this is

Python 3 / Flask controller for a chicken coop door on a headless
**Raspberry Pi Zero W, Raspberry Pi OS Bullseye (Python 3.9, Flask
1.1.2 from apt)**. It's a port of the NodeMCU firmware in the sibling
`coopdoor` repo, moved to the Pi because the NodeMCU's WiFi couldn't
hold a connection from the coop. The web UI, routes, `/status` JSON
keys and every safety behavior were kept deliberately identical. It
also replaces the older `chicken_door` Python program, which drove the
same relays on this same Pi; `install.sh` retires it.

Hardware: two relays, **BCM 17 = OPEN (DC+/CW), BCM 18 = CLOSE
(DC-/CCW), active-HIGH**, which is chicken_door's original wiring. No
position feedback (no limit switches), so the interlock, Stop and the
scheduler-defers-to-last-action design all exist to work around that.
Location hardcoded to Rhinelander, WI.

## Where things live

- **Source of truth for edits:** Nick's Windows folder
  `C:\Users\nicho_9uzywmc\Documents\Git\pi_door`, reached through the
  device bridge (`device_bash` sees it as `$HOME/mnt/Git/pi_door`).
  Sibling folders `coopdoor` (NodeMCU original) and `chicken_door` (old
  Pi program) are there for reference.
- **GitHub:** `nickfish03/pi_door` (Nick creates/pushes it; branch is
  `master`, as created locally). Check `git remote -v` and `git log`
  when picking this up.
- **On the Pi:** cloned to `~/git/pi_door` (by convention, matching the
  old `/home/pi/git/chicken_door`), running as `coopdoor.service`.
  Nick reaches the Pi over SSH; Claude does not, so anything on the Pi
  (installing, logs, `systemctl`) is Nick running commands Claude gives
  him.
- Runtime settings on the Pi: `/var/lib/coopdoor/settings.json`.

## Testing: unlike coopdoor, this CAN be verified

The cloud container can run everything except real GPIO. Reproduce the
Pi's exact stack with uv:

```
uv venv -p 3.9 venv39
uv pip install -p venv39/bin/python flask==1.1.2 werkzeug==1.0.1 jinja2==2.11.3 \
    markupsafe==1.1.1 itsdangerous==1.1.0 click==7.1.2
venv39/bin/python -m unittest discover -s tests -t .
```

Also worth running on current Python/Flask, so a future Bookworm
upgrade doesn't break it. For the real GPIO code path, put a stub
`RPi/GPIO.py` (constants plus logging functions) on `PYTHONPATH` and
run `python -m coopdoor` / `python -m coopdoor.relays_off`.
`--fake-gpio --port 8080` plus Playwright (`executable_path` from
`/opt/pw-browsers/chromium-*/chrome-linux/chrome`; don't run `playwright
install`) gives a screenshot of the UI. Edit in the cloud copy, test
there, then copy into the Windows folder. Run the tests before every
handoff.

Compatibility constraints, since apt's versions on Bullseye are old:
- **Python 3.9**: no `match`, no `X | Y` type unions, no 3.10+ stdlib.
- **Flask 1.1**: `@app.route(..., methods=[...])`, not `@app.get/post`.
- No pip installs on the Pi. Dependencies are apt packages only
  (`python3-flask`, `python3-rpi.gpio`). Don't add a dependency
  without a matching apt package and an `install.sh` update.

## Safety rules (don't regress these)

- **Never call `GPIO.cleanup()`.** It floats the pins. Pins stay
  outputs driven to OFF.
- Relays-off has four independent layers (timer, 1 s `door.update()`
  backstop, SIGTERM → exit → `shutdown()`, systemd `ExecStopPost`
  `relays_off`). Keep all four.
- Move timing uses `time.monotonic()`. Wall-clock time is only for
  schedule matching.
- The scheduler must not act until `clock.py` says NTP synced since
  boot. fake-hwclock makes a stale boot time look plausible. The
  synced state is latched, so a long WiFi outage doesn't stop the
  scheduler.
- The 0.25 s reverse dead-time (`RELAY_REVERSE_DEADTIME_S`) is
  intentional; the NodeMCU didn't have it.
- `net-watchdog.sh` must never reboot mid-move or more than once per
  30 min of uptime. It checks the router, not the internet.
- One `RLock` in `CoopController` serializes all door/settings
  mutations (Flask runs each request on its own thread, and the
  scheduler has its own thread).

## Conventions

- Heavy *why* comments, same voice as coopdoor. Keep it up.
- `static/index.html` is one self-contained file (no CDN, no build
  step, dark mode via `prefers-color-scheme`), ported from coopdoor's
  `WebPage.h`. Any `/status` field it reads must exist.
  `tests/test_web.py::test_status_has_every_field_the_page_reads`
  enforces that direction.
- Settings field names = `/status` JSON keys. New setting: add to
  `DEFAULTS` + `_valid()` in `settings.py`. No magic number needed;
  existing saved values are kept.
- `controller.py` stays Flask-free, so safety logic is unit-testable.
- Keep `README.md` in sync with structural changes.
- Line endings: `.gitattributes` forces LF. Shell scripts and systemd
  units break on the Pi with CRLF.

## Git notes (carried over from coopdoor's experience)

- `device_bash` can't delete in the connected folder without explicit
  permission, so git's cleanup of temp/lock files warns. Ignore the
  warnings if the exit code is 0. A real `index.lock` failure is usually
  VS Code's Source Control polling. Run `mv -f .git/index.lock
  .git/index.lock.stale 2>/dev/null` in the same command, just before
  the git call.
- The device VM has no global git identity. The repo's first commit was
  authored as `Nick Church <nick@toadtownservices.com>`, so commits made
  from `device_bash` pass that with `git -c user.name=... -c
  user.email=...`. Don't invent `*@users.noreply.github.com` addresses
  (see coopdoor's CLAUDE.md for why).
- In the device VM, `coopdoor` shows every file as modified. That's
  CRLF vs LF only (`git diff --ignore-cr-at-eol` is empty), not real
  changes.
