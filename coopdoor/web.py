# ============================================================================
# web.py - HTTP routes (Flask)
#
# Same routes, methods and responses as the NodeMCU's web server, so the
# page and any bookmarks/scripts behave identically:
#
#   GET  /         the single-page UI (static/index.html)
#   GET  /status   JSON snapshot the page polls every 5 s
#   POST /open     open (409 + JSON if it repeats the last action, unless ?confirm=1)
#   POST /close    close (same interlock)
#   POST /stop     stop immediately
#   POST /save     persist settings from the form
#
# Written for Flask 1.1 (what Raspberry Pi OS Bullseye's apt package
# ships) - so @app.route(..., methods=[...]), not the newer @app.post.
# ============================================================================

import os

from flask import Flask, jsonify, request, send_from_directory

from .controller import ConfirmNeeded, SaveError
from .settings import ACTION_CLOSED, ACTION_OPEN

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(ctl):
    app = Flask(__name__, static_folder=None)

    @app.after_request
    def no_cache(resp):
        # Always show live state, and pick up a new page right after a
        # `git pull` + restart instead of a stale cached copy.
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/", methods=["GET"])
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/status", methods=["GET"])
    def status():
        return jsonify(ctl.status())

    def _move(action):
        confirmed = request.values.get("confirm") == "1"
        try:
            ctl.manual_move(action, confirmed=confirmed)
        except ConfirmNeeded as e:
            return jsonify(needsConfirm=True, message=str(e)), 409
        return "OK", 200, {"Content-Type": "text/plain"}

    @app.route("/open", methods=["POST"])
    def open_door():
        return _move(ACTION_OPEN)

    @app.route("/close", methods=["POST"])
    def close_door():
        return _move(ACTION_CLOSED)

    @app.route("/stop", methods=["POST"])
    def stop_door():
        ctl.manual_stop()
        return "OK", 200, {"Content-Type": "text/plain"}

    @app.route("/save", methods=["POST"])
    def save():
        try:
            ctl.save_form(request.form)
        except SaveError as e:
            return "Not saved: %s" % e, 400, {"Content-Type": "text/plain"}
        return "OK", 200, {"Content-Type": "text/plain"}

    return app
