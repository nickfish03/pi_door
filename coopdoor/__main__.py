# ============================================================================
# Entry point:  python3 -m coopdoor  [--fake-gpio] [--port N]
#
# Normally started by systemd (deploy/coopdoor.service), not by hand.
# For a test run on any machine with no relays attached:
#
#   python3 -m coopdoor --fake-gpio --port 8080
#
# --fake-gpio also treats the clock as synced, so the scheduler runs.
# Settings for a by-hand run go in ./state/ (see config.STATE_DIR), never
# in the real /var/lib/coopdoor.
# ============================================================================

import argparse
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime

from . import config
from .clock import ClockWatcher
from .controller import CoopController
from .relays import make_relays
from .settings import Settings
from .web import create_app

log = logging.getLogger("coopdoor")


def software_version():
    """git commit of the running code, plus when this process started -
    the Pi equivalent of the NodeMCU footer's "firmware built" stamp, so
    you can confirm a `git pull` + restart actually took."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        rev = subprocess.run(
            ["git", "-C", repo, "describe", "--always", "--dirty", "--tags"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        rev = "unknown"
    return "%s, started %s" % (rev, datetime.now().strftime("%Y-%m-%d %H:%M"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="coopdoor", description="Chicken coop door controller")
    p.add_argument("--fake-gpio", action="store_true",
                   help="don't touch real GPIO (for testing off the Pi); also assumes the clock is synced")
    p.add_argument("--port", type=int, default=config.HTTP_PORT, help="HTTP port (default %(default)s)")
    args = p.parse_args(argv)

    # systemd's journal adds its own timestamps; keep lines short.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    # Flask's server logs every request - with the page polling every 5 s
    # that would bury the useful lines in the journal.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    log.info("Chicken coop door controller starting (%s)",
             "FAKE GPIO" if args.fake_gpio else "GPIO %d open / %d close, active-%s" % (
                 config.RELAY_OPEN_PIN, config.RELAY_CLOSE_PIN,
                 "HIGH" if config.RELAY_ACTIVE_HIGH else "LOW"))

    settings = Settings(config.SETTINGS_FILE)
    settings.load()

    ctl = CoopController(
        relays=make_relays(fake=args.fake_gpio),
        settings=settings,
        clock=ClockWatcher(assume_synced=args.fake_gpio),
        version=software_version(),
    )

    # systemd stops the service with SIGTERM. Turn it into a normal exit so
    # the finally: below runs and the relays get switched off. (If this
    # process is killed outright instead, systemd's ExecStopPost runs
    # relays_off as a backstop.)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        ctl.start()
        log.info("Web UI on port %d", args.port)
        create_app(ctl).run(host=config.HTTP_HOST, port=args.port,
                            threaded=True, use_reloader=False)
    finally:
        log.info("Shutting down - relays off")
        ctl.shutdown()


if __name__ == "__main__":
    main()
