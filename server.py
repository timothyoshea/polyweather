"""
PolyWeather Flask API Server
Serves scanner results as JSON and hosts a static frontend.

Usage:
    python server.py

Endpoints:
    GET /              — serves static/index.html
    GET /api/scan      — returns scanner results (JSON)
    GET /api/status    — returns scanner status
"""
import sys
import threading
import traceback
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

import config
from scanner import scan
from output import polymarket_url

app = Flask(__name__, static_folder="static")

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

# ── Scanner state (protected by lock) ────────────────────────────────────────

_lock = threading.Lock()
_state = {
    "status": "idle",           # idle | scanning | error
    "last_scan_time": None,     # ISO timestamp of last completed scan
    "last_scan_mode": None,     # "tomorrow" or "all"
    "last_scan_tier1": None,
    "results": None,            # cached list of opportunity dicts
    "error": None,              # error message if last scan failed
}


def _run_scan(mode, tier1_only):
    """Execute the scanner in a background thread."""
    global _state
    try:
        import time as _time
        t0 = _time.time()

        # Set config flags before scanning
        config.TOMORROW = (mode == "tomorrow")
        config.TIER1_ONLY = tier1_only
        config.JSON_OUT = True   # suppress print output
        config.DEBUG = False

        opps = scan()
        scan_duration = round(_time.time() - t0, 1)

        # Enrich each opportunity with a Polymarket URL
        for opp in opps:
            opp["url"] = polymarket_url(opp)

        sure_bets = [o for o in opps if o.get("bet_type") == "sure"]
        edge_bets = [o for o in opps if o.get("bet_type") == "edge"]

        with _lock:
            _state["status"] = "idle"
            _state["last_scan_time"] = datetime.now().isoformat()
            _state["last_scan_mode"] = mode
            _state["last_scan_tier1"] = tier1_only
            _state["results"] = {
                "timestamp": datetime.now().isoformat(),
                "mode": mode,
                "tier1_only": tier1_only,
                "total": len(opps),
                "sure_bets": len(sure_bets),
                "edge_bets": len(edge_bets),
                "scan_duration_seconds": scan_duration,
                "opportunities": opps,
            }
            _state["error"] = None

    except Exception as e:
        with _lock:
            _state["status"] = "error"
            _state["error"] = f"{type(e).__name__}: {e}"
            _state["results"] = None
        traceback.print_exc()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/scan")
def api_scan():
    mode = request.args.get("mode", "tomorrow")
    if mode not in ("tomorrow", "all"):
        return jsonify({"error": "mode must be 'tomorrow' or 'all'"}), 400

    tier1_only = request.args.get("tier1_only", "false").lower() == "true"
    force = request.args.get("force", "false").lower() == "true"

    with _lock:
        status = _state["status"]
        cached = _state["results"]
        cached_mode = _state["last_scan_mode"]
        cached_tier1 = _state["last_scan_tier1"]

    # If currently scanning, tell the client to wait
    if status == "scanning":
        return jsonify({"status": "scanning", "message": "Scan in progress, please retry shortly."})

    # Return cached results if they match the requested params (and no force)
    if (
        cached is not None
        and not force
        and cached_mode == mode
        and cached_tier1 == tier1_only
    ):
        return jsonify(cached)

    # Start a new scan in the background
    with _lock:
        _state["status"] = "scanning"
        _state["error"] = None

    thread = threading.Thread(target=_run_scan, args=(mode, tier1_only), daemon=True)
    thread.start()

    return jsonify({"status": "scanning", "message": "Scan started, please retry shortly."})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "status": _state["status"],
            "last_scan_time": _state["last_scan_time"],
            "last_scan_mode": _state["last_scan_mode"],
            "last_scan_tier1": _state["last_scan_tier1"],
            "error": _state["error"],
        })


# ── Error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  PolyWeather API Server")
    print("  http://localhost:3789")
    print("  Endpoints: /api/scan, /api/status\n")
    app.run(host="0.0.0.0", port=3789, debug=False)
