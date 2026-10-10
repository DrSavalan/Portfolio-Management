"""Read-only bridge from a Windows-hosted MetaTrader 5 terminal to the dashboard.

Run on the same Windows user session as MetaTrader 5:
    py -m pip install MetaTrader5
    set MT5_BRIDGE_TOKEN=<a-long-random-secret>
    py windows_mt5_bridge.py

Use a private network or a secure tunnel. Never expose this process directly to
an untrusted public network. The dashboard must receive both MT5_BRIDGE_URL and
MT5_BRIDGE_TOKEN in its runtime environment.
"""

import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5

HOST = os.environ.get("MT5_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("MT5_BRIDGE_PORT", "8765"))
TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "")
MAX_BARS = 2000
SYMBOL_CANDIDATES = {
    "XAUUSD": ("XAUUSD", "GOLD", "XAUUSD.a", "GOLD.a"),
    "XAGUSD": ("XAGUSD", "SILVER", "XAGUSD.a"),
    "Nasdaq100": ("Nasdaq100", "US100", "NAS100", "USTEC", "NDX", "Nasdaq100.cash", "US100.cash"),
    "XCOPPER": ("XCUUSD", "COPPER", "HG", "XCUUSD.a"),
    "BRENT": ("BRENT", "UKOIL", "XBRUSD", "BRENT.a"),
    "USDJPY": ("USDJPY", "USDJPY.a"),
    "USDCHF": ("USDCHF", "USDCHF.a"),
    "EURUSD": ("EURUSD", "EURUSD.a"),
}
TIMEFRAMES = {"M15": mt5.TIMEFRAME_M15}


def resolve_symbol(key):
    for candidate in SYMBOL_CANDIDATES.get(key, (key,)):
        info = mt5.symbol_info(candidate)
        if info is None:
            continue
        if not info.visible and not mt5.symbol_select(candidate, True):
            continue
        return candidate
    return None


def rates(symbol_key, timeframe, count):
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported timeframe")
    symbol = resolve_symbol(symbol_key)
    if symbol is None:
        raise LookupError("symbol not found in MT5")
    data = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, count)
    if data is None:
        raise RuntimeError(f"MT5 returned no rates: {mt5.last_error()}")
    return [
        {"time": int(row["time"]), "open": float(row["open"]), "high": float(row["high"]),
         "low": float(row["low"]), "close": float(row["close"])}
        for row in data
    ]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def respond(self, status, body):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        value = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return value.startswith(prefix) and hmac.compare_digest(value[len(prefix):], TOKEN)

    def do_GET(self):
        if not self.authorized():
            self.respond(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.respond(HTTPStatus.OK, {"status": "ok", "mt5": True})
            return
        if parsed.path != "/rates":
            self.respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        query = parse_qs(parsed.query)
        try:
            symbol = query.get("symbol", [""])[0]
            timeframe = query.get("timeframe", [""])[0]
            count = int(query.get("count", ["1000"])[0])
            if count < 50 or count > MAX_BARS:
                raise ValueError("count must be between 50 and 2000")
            self.respond(HTTPStatus.OK, {"rates": rates(symbol, timeframe, count)})
        except (ValueError, LookupError, RuntimeError) as exc:
            self.respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main():
    if not TOKEN or len(TOKEN) < 24:
        raise SystemExit("Set MT5_BRIDGE_TOKEN to a random value of at least 24 characters.")
    if not mt5.initialize():
        raise SystemExit(f"Could not initialise MT5: {mt5.last_error()}")
    print(f"MT5 bridge listening on {HOST}:{PORT}")
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
