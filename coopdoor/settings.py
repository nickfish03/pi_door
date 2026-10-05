# ============================================================================
# settings.py - everything you change from the web UI, persisted to disk
#
# Replaces the NodeMCU's EEPROM struct. Stored as JSON (human-readable:
# `cat /var/lib/coopdoor/settings.json`), so there's no SETTINGS_MAGIC to
# bump any more: a field added in a later version just picks up its
# default, and an unknown or out-of-range value is replaced by its default
# rather than trusted. Your saved settings survive upgrades.
#
# Saves are atomic (write a temp file, fsync, rename over the old one), so
# a power cut in the middle of a save leaves either the old file or the new
# one - never a half-written one. That matters on a Pi in a coop, where the
# power can go out at any moment.
# ============================================================================

import json
import logging
import os
import tempfile
import threading

from . import config

log = logging.getLogger(__name__)

MODE_MANUAL = "manual"   # fixed daily HH:MM open/close times
MODE_SUN = "sun"         # relative to sunrise/sunset, with offsets

ACTION_UNKNOWN = "unknown"
ACTION_OPEN = "open"
ACTION_CLOSED = "closed"

# Field -> default. Field names are the same as the /status JSON keys the
# web page already uses, so the two can't drift apart.
DEFAULTS = {
    # How long to energize each relay. 10 s matches the old chicken_door
    # program's `motion = 10` for this same hardware - tune from the UI.
    "openDurationMs": 10000,
    "closeDurationMs": 10000,

    "schedulerEnabled": True,   # False = manual-only (buttons still work)
    "mode": MODE_SUN,

    "manualOpenHour": 7,
    "manualOpenMin": 0,
    "manualCloseHour": 20,
    "manualCloseMin": 0,

    # Minutes, may be negative: +30 = 30 min after sunrise,
    # -15 = 15 min before sunset.
    "sunOpenOffsetMin": 30,
    "sunCloseOffsetMin": -15,

    # Safety interlock: the last commanded action. Unlike the NodeMCU
    # (which kept the timestamp/source in RAM only), all three are
    # persisted, so after a reboot the UI still shows when and how the door
    # last moved.
    "lastAction": ACTION_UNKNOWN,
    "lastActionAt": "",
    "lastActionSource": "",
}


def _valid(key, value):
    """True if value is acceptable for key. Used both when loading the file
    and when accepting values from the web form."""
    if key in ("openDurationMs", "closeDurationMs"):
        return (isinstance(value, int) and not isinstance(value, bool)
                and config.MIN_DURATION_MS <= value <= config.MAX_DURATION_MS)
    if key == "schedulerEnabled":
        return isinstance(value, bool)
    if key == "mode":
        return value in (MODE_MANUAL, MODE_SUN)
    if key in ("manualOpenHour", "manualCloseHour"):
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23
    if key in ("manualOpenMin", "manualCloseMin"):
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 59
    if key in ("sunOpenOffsetMin", "sunCloseOffsetMin"):
        return (isinstance(value, int) and not isinstance(value, bool)
                and abs(value) <= config.MAX_SUN_OFFSET_MIN)
    if key == "lastAction":
        return value in (ACTION_UNKNOWN, ACTION_OPEN, ACTION_CLOSED)
    if key in ("lastActionAt", "lastActionSource"):
        return isinstance(value, str)
    return False


class Settings:
    """Dict-like holder for the current settings. Access as s["mode"]."""

    def __init__(self, path=config.SETTINGS_FILE):
        self.path = path
        self._data = dict(DEFAULTS)
        self._save_lock = threading.Lock()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if key not in DEFAULTS:
            raise KeyError(key)
        if not _valid(key, value):
            raise ValueError("invalid value for %s: %r" % (key, value))
        self._data[key] = value

    def as_dict(self):
        return dict(self._data)

    def load(self):
        """Load from disk. A missing or unreadable file means defaults
        (and writes them out); individual bad fields fall back to their
        defaults with a warning."""
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("top level is not an object")
        except FileNotFoundError:
            log.info("No settings file at %s yet - using defaults", self.path)
            self.save()
            return
        except (OSError, ValueError) as e:
            log.error("Settings file %s unreadable (%s) - using defaults", self.path, e)
            self.save()
            return

        for key, default in DEFAULTS.items():
            if key in raw and _valid(key, raw[key]):
                self._data[key] = raw[key]
            else:
                if key in raw:
                    log.warning("Ignoring bad saved value %s=%r, using default %r",
                                key, raw[key], default)
                self._data[key] = default
        log.info("Settings loaded from %s", self.path)

    def save(self):
        directory = os.path.dirname(self.path) or "."
        with self._save_lock:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self._data, f, indent=2, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            # fsync the directory too, so the rename itself survives a
            # power cut, not just the file contents.
            try:
                dfd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
