# ============================================================================
# relays_off.py - force both relays off and exit
#
# Run by systemd before the controller starts (ExecStartPre) and after it
# stops for ANY reason (ExecStopPost) - including a crash or a kill -9,
# where the controller's own cleanup never gets a chance to run. A GPIO pin
# keeps whatever level it had when its process died, so without this a
# crash mid-move could leave the motor powered indefinitely.
#
#   python3 -m coopdoor.relays_off
# ============================================================================

from .relays import PiRelays


def main():
    PiRelays().all_off()
    print("coopdoor: both relays forced off")


if __name__ == "__main__":
    main()
