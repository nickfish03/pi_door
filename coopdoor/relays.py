# ============================================================================
# relays.py - the only code that touches GPIO
#
# Two implementations with the same three methods:
#   PiRelays   - real hardware, via RPi.GPIO (preinstalled on Raspberry Pi
#                OS Bullseye, and what the old chicken_door used)
#   FakeRelays - in-memory stand-in for testing on any machine; logs what
#                the real one would have done
#
# Nothing here knows about doors or timing - see door.py for that.
# ============================================================================

import logging

from . import config

log = logging.getLogger(__name__)


class PiRelays:
    def __init__(self, open_pin=config.RELAY_OPEN_PIN, close_pin=config.RELAY_CLOSE_PIN,
                 active_high=config.RELAY_ACTIVE_HIGH):
        import RPi.GPIO as GPIO  # imported here so tests never need it

        self._GPIO = GPIO
        self.open_pin = open_pin
        self.close_pin = close_pin
        self._on = GPIO.HIGH if active_high else GPIO.LOW
        self._off = GPIO.LOW if active_high else GPIO.HIGH

        GPIO.setwarnings(False)  # "channel already in use" is expected after a restart
        GPIO.setmode(GPIO.BCM)
        # initial= sets the level atomically with switching the pin to an
        # output, so there's no instant where the pin is an output at the
        # wrong level.
        GPIO.setup(self.open_pin, GPIO.OUT, initial=self._off)
        GPIO.setup(self.close_pin, GPIO.OUT, initial=self._off)

    def energize_open(self):
        self._GPIO.output(self.close_pin, self._off)  # never both at once
        self._GPIO.output(self.open_pin, self._on)

    def energize_close(self):
        self._GPIO.output(self.open_pin, self._off)
        self._GPIO.output(self.close_pin, self._on)

    def all_off(self):
        self._GPIO.output(self.open_pin, self._off)
        self._GPIO.output(self.close_pin, self._off)

    # Deliberately no GPIO.cleanup() anywhere: cleanup() turns the pins back
    # into floating inputs, and a floating relay-board input can chatter. A
    # pin left as an output driven to the OFF level stays that way after the
    # process exits, which is exactly what we want.


class FakeRelays:
    def __init__(self, *args, **kwargs):
        self.open_on = False
        self.close_on = False
        self.history = []  # list of (open_on, close_on) after each change, for tests

    def _set(self, open_on, close_on):
        self.open_on, self.close_on = open_on, close_on
        self.history.append((open_on, close_on))
        log.info("[fake GPIO] open relay %s, close relay %s",
                 "ON" if open_on else "off", "ON" if close_on else "off")

    def energize_open(self):
        self._set(True, False)

    def energize_close(self):
        self._set(False, True)

    def all_off(self):
        self._set(False, False)


def make_relays(fake=False):
    return FakeRelays() if fake else PiRelays()
