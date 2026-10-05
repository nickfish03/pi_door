# ============================================================================
# controller.py - the door's brain: interlock, scheduler, status
#
# This is the Python equivalent of most of the NodeMCU's main.cpp (minus
# WiFi/OTA/HTTP plumbing, which the OS and Flask now handle). It is
# deliberately independent of Flask, so all of the safety logic can be
# tested without a web server or real hardware - see tests/.
#
# Threads: Flask serves each web request on its own thread, and a
# background thread runs the scheduler. One lock (self._lock) serializes
# every action that reads or changes door/settings state, so a scheduled
# move and a button click can never interleave halfway.
# ============================================================================

import logging
import threading
from datetime import datetime

from . import config
from .door import DoorController
from .settings import (ACTION_CLOSED, ACTION_OPEN, ACTION_UNKNOWN, MODE_MANUAL, MODE_SUN,
                       Settings)
from .suntimes import compute_sun_times, format_minutes

log = logging.getLogger(__name__)


class ConfirmNeeded(Exception):
    """Raised when a manual move would repeat the last action and wasn't
    explicitly confirmed. The message is shown to the user."""


class SaveError(Exception):
    """Raised when /save gets a value it can't accept. Nothing is saved."""


class DoorBusy(Exception):
    """Raised when a fine-tuning nudge is requested while the door is
    already moving."""


JOG_UP = "up"
JOG_DOWN = "down"


class CoopController:
    def __init__(self, relays, settings, clock, now_fn=datetime.now, version="unknown"):
        self.settings = settings
        self.door = DoorController(relays)
        self.clock = clock
        self._now = now_fn          # injectable for tests
        self.version = version
        self._lock = threading.RLock()

        # Sunrise/sunset cache, recomputed when the calendar date changes.
        self._sun_date = None
        self._sun = (None, None)

        # Date (ordinal) we last handled the open/close event on, so each
        # fires at most once a day. Same idea as lastOpenTriggerYday.
        self._last_open_day = None
        self._last_close_day = None

        # Fine tuning (Up/Down nudges). Which door move is a nudge (so Stop
        # can treat it differently), its direction (+1 up / -1 down), and the
        # net nudging since the last full open/close, in ms (+ = up). That
        # running total is shown on the page as a tuning aid: if you always
        # nudge +1500 ms up after an Open, the Open duration is 1500 ms short.
        # RAM only - it's a hint, not state worth persisting.
        self._jog_move_id = None
        self._jog_sign = 0
        self._jog_net_ms = 0

        self._stop_event = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------
    # Schedule math
    # ------------------------------------------------------------------
    def _sun_for(self, d):
        if d != self._sun_date:
            self._sun = compute_sun_times(d)
            self._sun_date = d
        return self._sun

    def todays_targets(self, now):
        """(open_min, close_min) since local midnight for today, either
        possibly None. Shared by the scheduler and /status, so the UI
        always shows exactly what the scheduler will act on."""
        s = self.settings
        if s["mode"] == MODE_MANUAL:
            return (s["manualOpenHour"] * 60 + s["manualOpenMin"],
                    s["manualCloseHour"] * 60 + s["manualCloseMin"])
        sunrise, sunset = self._sun_for(now.date())
        open_t = None if sunrise is None else (sunrise + s["sunOpenOffsetMin"]) % 1440
        close_t = None if sunset is None else (sunset + s["sunCloseOffsetMin"]) % 1440
        return open_t, close_t

    # ------------------------------------------------------------------
    # Interlock bookkeeping
    # ------------------------------------------------------------------
    def _record_action(self, action, source):
        """Record and persist what just happened. Called when a move is
        TRIGGERED, not when it ends: a timed move has no real "done"
        signal, and the interlock must hold for the whole window."""
        s = self.settings
        s["lastAction"] = action
        self._jog_net_ms = 0  # a full move (or an aborted one) starts a new tuning tally
        if self.clock.is_synced():
            s["lastActionAt"] = self._now().strftime("%Y-%m-%d %H:%M")
        else:
            s["lastActionAt"] = "unknown time (clock not synced)"
        s["lastActionSource"] = source
        try:
            s.save()
        except OSError as e:
            # The move itself already happened; don't fail it over this.
            log.error("Could not save settings after %s: %s", action, e)

    # ------------------------------------------------------------------
    # Manual actions (web UI)
    # ------------------------------------------------------------------
    def manual_move(self, action, confirmed=False):
        """action is ACTION_OPEN or ACTION_CLOSED. Raises ConfirmNeeded if
        it would repeat the last recorded action without confirmation."""
        word = "OPEN" if action == ACTION_OPEN else "CLOSED"
        with self._lock:
            s = self.settings
            if s["lastAction"] == action and not confirmed:
                raise ConfirmNeeded(
                    "Door's last recorded action was already %s (%s, %s). Running it again "
                    "risks over-driving the actuator past the end of travel. Resend with "
                    "confirm=1 if you're sure." % (
                        word,
                        s["lastActionAt"] or "unknown time",
                        s["lastActionSource"] or "unknown source"))
            if action == ACTION_OPEN:
                self.door.trigger_open(s["openDurationMs"])
            else:
                self.door.trigger_close(s["closeDurationMs"])
            self._record_action(action, "manual")

    def manual_stop(self):
        with self._lock:
            if self.door.is_moving() and self.door.move_id == self._jog_move_id:
                # Stopping a nudge: it was only ever a small adjustment, so
                # the open/closed record still stands. Take the part that
                # didn't run back off the tuning tally.
                self._jog_net_ms -= self._jog_sign * self.door.remaining_ms()
                self.door.stop()
                log.info("Fine-tune nudge stopped early")
                return
            if self.door.stop():
                # Stopped mid-travel: the door could be anywhere, so the
                # interlock can no longer claim to know its position.
                self._record_action(ACTION_UNKNOWN, "manual (stopped mid-move)")

    def jog(self, direction, ms):
        """Fine tuning: run the motor up (open relay) or down (close relay)
        for a short time, to adjust the door's height.

        Deliberately NOT subject to the confirm-before-repeat interlock, and
        doesn't change lastAction: nudging an open door up a little is the
        whole point, and afterwards the door is still "open" for the
        scheduler's purposes. The size limit (config.JOG_MAX_MS) is what
        keeps this from being used as a full move. Refused while the door is
        already moving, so a nudge can never replace or reverse a full move
        partway through."""
        if direction not in (JOG_UP, JOG_DOWN):
            raise SaveError("direction must be 'up' or 'down'")
        try:
            ms = int(str(ms).strip())
        except ValueError:
            raise SaveError("step must be a whole number of ms")
        if not config.JOG_MIN_MS <= ms <= config.JOG_MAX_MS:
            raise SaveError("step must be %d-%d ms" % (config.JOG_MIN_MS, config.JOG_MAX_MS))

        with self._lock:
            if self.door.is_moving():
                raise DoorBusy("Door is already moving - wait for it to finish, or press Stop first.")
            if direction == JOG_UP:
                self.door.trigger_open(ms)
                self._jog_sign = 1
            else:
                self.door.trigger_close(ms)
                self._jog_sign = -1
            self._jog_move_id = self.door.move_id
            self._jog_net_ms += self._jog_sign * ms
            log.info("Fine-tune nudge %s %d ms (net since last full move: %+d ms)",
                     direction, ms, self._jog_net_ms)

    def save_form(self, form):
        """Apply /save form fields (same names the NodeMCU page posts).
        All-or-nothing: if any present field is invalid, raises SaveError
        and changes nothing."""
        updates = {}
        errors = []

        def as_int(name):
            try:
                return int(str(form[name]).strip())
            except ValueError:
                errors.append("%s: not a whole number" % name)
                return None

        def hhmm(name):
            raw = str(form[name]).strip()
            try:
                h, m = raw.split(":")
                h, m = int(h), int(m)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return h, m
            except ValueError:
                pass
            errors.append("%s: expected HH:MM" % name)
            return None

        if "openDur" in form:
            v = as_int("openDur")
            if v is not None:
                if config.MIN_DURATION_MS <= v <= config.MAX_DURATION_MS:
                    updates["openDurationMs"] = v
                else:
                    errors.append("open duration must be %d-%d ms" %
                                  (config.MIN_DURATION_MS, config.MAX_DURATION_MS))
        if "closeDur" in form:
            v = as_int("closeDur")
            if v is not None:
                if config.MIN_DURATION_MS <= v <= config.MAX_DURATION_MS:
                    updates["closeDurationMs"] = v
                else:
                    errors.append("close duration must be %d-%d ms" %
                                  (config.MIN_DURATION_MS, config.MAX_DURATION_MS))
        if "schedulerEnabled" in form:
            updates["schedulerEnabled"] = (form["schedulerEnabled"] == "on")
        if "mode" in form:
            updates["mode"] = MODE_SUN if form["mode"] == "sun" else MODE_MANUAL
        if "manOpen" in form:
            hm = hhmm("manOpen")
            if hm:
                updates["manualOpenHour"], updates["manualOpenMin"] = hm
        if "manClose" in form:
            hm = hhmm("manClose")
            if hm:
                updates["manualCloseHour"], updates["manualCloseMin"] = hm
        for field, key in (("sunOpenOffset", "sunOpenOffsetMin"),
                           ("sunCloseOffset", "sunCloseOffsetMin")):
            if field in form:
                v = as_int(field)
                if v is not None:
                    if abs(v) <= config.MAX_SUN_OFFSET_MIN:
                        updates[key] = v
                    else:
                        errors.append("%s must be within +/-%d min" %
                                      (field, config.MAX_SUN_OFFSET_MIN))

        if errors:
            raise SaveError("; ".join(errors))

        with self._lock:
            for k, v in updates.items():
                self.settings[k] = v
            self.settings.save()
            # A change may move today's targets or switch modes; clear the
            # day latches so the new schedule can still fire today if its
            # time hasn't passed yet. (Same behavior as the NodeMCU.)
            self._last_open_day = None
            self._last_close_day = None
        log.info("Settings saved: %s", updates)

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    def scheduler_tick(self):
        """One scheduler pass. Called about once a second by the
        background thread (and directly by tests)."""
        self.door.update()  # relay-timeout backstop, every tick regardless

        with self._lock:
            s = self.settings
            if not s["schedulerEnabled"]:
                return
            if not self.clock.is_synced():
                return  # don't act on a clock we can't trust

            now = self._now()
            today = now.toordinal()
            now_min = now.hour * 60 + now.minute
            open_t, close_t = self.todays_targets(now)

            # Same interlock as manual moves, minus the prompt (nobody's
            # there to answer it): if the last action already matches, skip
            # the redundant move but still mark today's event handled.
            if open_t is not None and now_min == open_t and self._last_open_day != today:
                if s["lastAction"] == ACTION_OPEN:
                    log.info("Scheduler: last action was already OPEN, skipping scheduled OPEN")
                    self._last_open_day = today
                elif not self.door.is_moving():
                    log.info("Scheduler: triggering OPEN")
                    self.door.trigger_open(s["openDurationMs"])
                    self._record_action(ACTION_OPEN, "schedule")
                    self._last_open_day = today

            if close_t is not None and now_min == close_t and self._last_close_day != today:
                if s["lastAction"] == ACTION_CLOSED:
                    log.info("Scheduler: last action was already CLOSED, skipping scheduled CLOSE")
                    self._last_close_day = today
                elif not self.door.is_moving():
                    log.info("Scheduler: triggering CLOSE")
                    self.door.trigger_close(s["closeDurationMs"])
                    self._record_action(ACTION_CLOSED, "schedule")
                    self._last_close_day = today

    def _run(self):
        log.info("Scheduler thread started")
        while not self._stop_event.wait(config.SCHEDULER_TICK_S):
            try:
                self.scheduler_tick()
            except Exception:
                # Never let one bad tick kill the scheduler thread.
                log.exception("Scheduler tick failed")

    def start(self):
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop_event.set()
        self.door.stop()

    # ------------------------------------------------------------------
    # Status for the web UI (same keys the NodeMCU's /status sent)
    # ------------------------------------------------------------------
    def status(self):
        with self._lock:
            now = self._now()
            s = self.settings
            sunrise, sunset = self._sun_for(now.date())
            open_t, close_t = self.todays_targets(now)
            return {
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "clockSynced": self.clock.is_synced(),
                "timeSource": self.clock.time_source(),
                "doorState": self.door.state,
                "lastAction": s["lastAction"],
                "lastActionAt": s["lastActionAt"],
                "lastActionSource": s["lastActionSource"],
                "schedulerEnabled": s["schedulerEnabled"],
                "mode": s["mode"],
                "openDurationMs": s["openDurationMs"],
                "closeDurationMs": s["closeDurationMs"],
                "manualOpenHour": s["manualOpenHour"],
                "manualOpenMin": s["manualOpenMin"],
                "manualCloseHour": s["manualCloseHour"],
                "manualCloseMin": s["manualCloseMin"],
                "sunOpenOffsetMin": s["sunOpenOffsetMin"],
                "sunCloseOffsetMin": s["sunCloseOffsetMin"],
                "sunriseTime": format_minutes(sunrise),
                "sunsetTime": format_minutes(sunset),
                "calcOpenTime": format_minutes(open_t),
                "calcCloseTime": format_minutes(close_t),
                "jogNetMs": self._jog_net_ms,
                "version": self.version,
            }
