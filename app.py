"""Flask dashboard for 15-minute MT5 portfolio allocation."""

import atexit
import threading
import time

from flask import Flask, jsonify, render_template, request

from mt5_data import BARS, TIMEFRAMES, load_market
from optimizer import Engine, OBJECTIVES

app = Flask(__name__)
engine = Engine()
_engines = {("short", "duration"): engine}
_lock = threading.Lock()
_optimization_lock = threading.Lock()
_state = {"result": None, "snapshot": None, "results": {}, "updated_at": 0.0, "busy": False, "progress": [], "profile": "short"}
INTERVAL = 300


def _refresh(profile="short", reserved=False):
    with _lock:
        if _state["busy"] and not reserved:
            return
        _state["busy"] = True
        _state["progress"] = [{"key": "mt5", "label": "MT5 terminal", "status": "loading", "detail": "Starting local terminal connection", "at": time.time()}]

    def report(event):
        with _lock:
            _state["progress"].append(event)

    try:
        snapshot = load_market(profile, progress=report)
        if not snapshot.error:
            report({"key": "optimization", "label": "Portfolio optimization", "status": "loading", "detail": "Aligning trading times and minimizing drawdown duration", "at": time.time()})
        result = _engines.setdefault((profile, "duration"), Engine()).run_cycle(snapshot, profile=profile)
        if not snapshot.error:
            report({"key": "optimization", "label": "Portfolio optimization", "status": "done" if result["ready"] else "error", "detail": f"Optimized on {result['bars']:,} aligned intervals" if result["ready"] else result["error"], "at": time.time()})
        with _lock:
            _state["result"] = result
            _state["snapshot"] = snapshot if result["ready"] else None
            _state["results"] = {"duration": result} if result["ready"] else {}
            _state["profile"] = profile
            _state["updated_at"] = time.time()
    finally:
        with _lock:
            _state["busy"] = False


def _scheduler():
    while True:
        try:
            with _lock:
                profile = _state["profile"]
            _refresh(profile)
        except Exception as exc:
            app.logger.error("Portfolio refresh failed: %s", exc)
            with _lock:
                _state["result"] = {"ready": False, "error": str(exc)}
                _state["snapshot"] = None
                _state["results"] = {}
                _state["updated_at"] = time.time()
                _state["progress"].append({"key": "mt5", "label": "MT5 terminal", "status": "error", "detail": str(exc), "at": time.time()})
        time.sleep(INTERVAL)


_started = False
_start_lock = threading.Lock()


def _ensure_started():
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        _started = True
        thread = threading.Thread(target=_scheduler, daemon=True, name="portfolio-refresh")
        thread.start()


@app.before_request
def _boot():
    _ensure_started()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def portfolio():
    objective = request.args.get("objective", "duration")
    profile = request.args.get("profile", "short")
    if objective not in OBJECTIVES or profile not in TIMEFRAMES:
        return jsonify({"error": "Unknown portfolio selection"}), 400
    with _lock:
        if profile != _state["profile"] and not _state["busy"]:
            _state["busy"] = True
            threading.Thread(target=_refresh, args=(profile, True), daemon=True, name=f"portfolio-refresh-{profile}").start()
        current = profile == _state["profile"]
        result = _state["result"] if current else None
        snapshot = _state["snapshot"] if current else None
        cached = _state["results"].get(objective) if current else None
        busy = _state["busy"]
        updated_at = _state["updated_at"]
        progress = list(_state["progress"])
    if result is None:
        return jsonify({"ready": False, "busy": busy, "progress": progress, "message": "Analysing market data…"})
    if objective != "duration" and snapshot is not None and cached is None:
        with _optimization_lock:
            with _lock:
                cached = _state["results"].get(objective) if _state["snapshot"] is snapshot else None
            if cached is None:
                cached = _engines.setdefault((profile, objective), Engine()).run_cycle(snapshot, objective, profile)
                with _lock:
                    if _state["snapshot"] is snapshot:
                        _state["results"][objective] = cached
    response = dict(cached or result)
    response["next_refresh_at"] = updated_at + INTERVAL
    response["busy"] = busy
    response["profile"] = profile
    response["timeframe"] = TIMEFRAMES[profile][0]
    response["bars_requested"] = BARS
    response["progress"] = progress
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
