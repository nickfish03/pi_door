# ============================================================================
# door.py - time-based door control
#
# Energizes one of two relays for a fixed duration (milliseconds), then
# stops. No position feedback of any kind - the caller is trusted to give a
# sane duration. Same model as the NodeMCU's DoorController.
#
# Turning the relay off is done two independent ways, so either one alone
# is enough:
#   1. a threading.Timer set for exactly the move duration, and
#   2. update(), called once a second by the scheduler thread, which stops
#      any move whose deadline has passed (a backstop in case a timer
#      thread ever fails to fire).
#
# Timing uses time.monotonic(), which never jumps when NTP adjusts the
# wall clock, so a clock correction mid-move can't lengthen or shorten it.
# ============================================================================

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)

IDLE = "idle"
OPENING = "opening"
CLOSING = "closing"


class DoorController:
    def __init__(self, relays, reverse_deadtime_s=config.RELAY_REVERSE_DEADTIME_S):
        self._relays = relays
        self._deadtime = reverse_deadtime_s
        self._lock = threading.RLock()
        self._state = IDLE
        self._deadline = 0.0
        self._move_id = 0          # identifies the current move, so a stale
        self._timer = None         # timer from an earlier move can't stop a newer one
        self._relays.all_off()

    # -- queries ----------------------------------------------------------
    @property
    def state(self):
        return self._state

    def is_moving(self):
        return self._state != IDLE

    @property
    def move_id(self):
        """Increments with every move started; lets callers tell which
        move is the current one."""
        return self._move_id

    def remaining_ms(self):
        """Milliseconds left in the current move (0 when idle)."""
        with self._lock:
            if not self.is_moving():
                return 0
            return max(0, int(round((self._deadline - time.monotonic()) * 1000)))

    # -- commands ---------------------------------------------------------
    def trigger_open(self, duration_ms):
        return self._start(OPENING, duration_ms)

    def trigger_close(self, duration_ms):
        return self._start(CLOSING, duration_ms)

    def stop(self):
        """De-energize both relays immediately. Returns True if a move was
        actually in progress (the caller uses that to decide whether the
        door's position is now uncertain)."""
        with self._lock:
            was_moving = self.is_moving()
            self._stop_locked()
            return was_moving

    def update(self):
        """Backstop: stop a move whose time is up. Safe to call any time."""
        with self._lock:
            if self.is_moving() and time.monotonic() >= self._deadline:
                log.warning("Move deadline passed without the timer stopping it - stopping now")
                self._stop_locked()

    # -- internals --------------------------------------------------------
    def _start(self, new_state, duration_ms):
        if duration_ms <= 0:
            return False
        with self._lock:
            reversing = self.is_moving() and self._state != new_state
            self._cancel_timer()
            self._relays.all_off()
            if reversing and self._deadtime > 0:
                # Brief pause with everything off before reversing polarity
                # (see RELAY_REVERSE_DEADTIME_S in config.py).
                time.sleep(self._deadtime)

            if new_state == OPENING:
                self._relays.energize_open()
            else:
                self._relays.energize_close()

            self._state = new_state
            self._move_id += 1
            duration_s = duration_ms / 1000.0
            self._deadline = time.monotonic() + duration_s
            self._timer = threading.Timer(duration_s, self._timer_fired, args=(self._move_id,))
            self._timer.daemon = True
            self._timer.start()
            log.info("Door %s for %d ms", new_state, duration_ms)
            return True

    def _timer_fired(self, move_id):
        with self._lock:
            if move_id == self._move_id and self.is_moving():
                self._stop_locked()
                log.info("Move finished (duration elapsed)")

    def _stop_locked(self):
        self._cancel_timer()
        self._relays.all_off()
        self._state = IDLE

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
