"""Download selected-timeframe candles from local MT5 and persist Excel."""

import os
import time
from dataclasses import dataclass, field

import pandas as pd

BARS = 8_760
TIMEFRAMES = {"short": ("M15", 15, 365.25 / 4), "medium": ("H1", 60, 365.25), "long": ("H4", 240, 365.25 * 4)}
EXCEL_DIR = os.path.join(os.path.dirname(__file__), "data")


@dataclass(frozen=True)
class SymbolSpec:
    key: str
    label: str
    emoji: str
    candidates: tuple
    pip: float
    color: str


@dataclass
class MarketSnapshot:
    source: str
    frames: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    error: str = ""


SYMBOLS = [
    SymbolSpec("XAUUSD", "Gold", "🥇", ("XAUUSD", "GOLD", "XAUUSD.a", "GOLD.a"), 0.1, "#f5c451"),
    SymbolSpec("XAGUSD", "Silver", "🥈", ("XAGUSD", "SILVER", "XAGUSD.a"), 0.01, "#c8d4e0"),
    SymbolSpec("Nasdaq100", "Nasdaq100", "📈", ("Nasdaq100", "US100", "NAS100", "USTEC", "NDX", "Nasdaq100.cash", "US100.cash"), 1.0, "#7aa2f7"),
    SymbolSpec("XCOPPER", "Copper", "🟠", ("XCUUSD", "COPPER", "HG", "XCUUSD.a"), 0.001, "#e08a4a"),
    SymbolSpec("BRENT", "Brent", "🛢️", ("BRENT", "UKOIL", "XBRUSD", "BRENT.a"), 0.01, "#6fbf8f"),
    SymbolSpec("COFFEE", "Coffee", "☕", ("COFFEE", "Coffee", "COFFEE.a", "COFFEE.cash", "KC"), 0.01, "#b27954"),
    SymbolSpec("COCOA", "Cocoa", "🍫", ("COCOA", "Cocoa", "COCOA.a", "COCOA.cash", "CC"), 0.01, "#b77a9b"),
    SymbolSpec("USDJPY", "USD / JPY", "🇺🇸🇯🇵", ("USDJPY", "USDJPY.a"), 0.01, "#e0685f"),
    SymbolSpec("USDCHF", "USD / CHF", "🇺🇸🇨🇭", ("USDCHF", "USDCHF.a"), 0.0001, "#b48ce0"),
    SymbolSpec("EURUSD", "EUR / USD", "🇪🇺🇺🇸", ("EURUSD", "EURUSD.a"), 0.0001, "#5fb8d6"),
]
ALL_KEYS = [spec.key for spec in SYMBOLS]


def _report(callback, key, label, status, detail=""):
    if callback is not None:
        callback({"key": key, "label": label, "status": status, "detail": detail, "at": time.time()})


def _mt5_module():
    try:
        import MetaTrader5 as mt5
        return mt5
    except ImportError:
        return None


def _resolve_mt5_symbol(mt5, spec):
    for name in spec.candidates:
        info = mt5.symbol_info(name)
        if info is not None:
            if not info.visible and not mt5.symbol_select(name, True):
                continue
            return name
    return None


def fetch_from_mt5(profile="short", progress=None) -> MarketSnapshot:
    timeframe = TIMEFRAMES[profile][0]
    mt5 = _mt5_module()
    if mt5 is None:
        error = "MetaTrader5 is not available here. Run Flask on the Windows machine with MT5 open and install MetaTrader5 in that Python environment."
        _report(progress, "mt5", "MT5 terminal", "error", error)
        return MarketSnapshot(source="unavailable", error=error)

    _report(progress, "mt5", "MT5 terminal", "loading", "Connecting to local terminal")
    if not mt5.initialize():
        error = f"MT5 initialize failed: {mt5.last_error()}"
        _report(progress, "mt5", "MT5 terminal", "error", error)
        return MarketSnapshot(source="unavailable", error=error)
    _report(progress, "mt5", "MT5 terminal", "done", "Connected to local terminal")

    frames, missing = {}, []
    try:
        for index, spec in enumerate(SYMBOLS, start=1):
            _report(progress, spec.key, spec.label, "loading", f"Downloading {BARS:,} {timeframe} candles · {index} of {len(SYMBOLS)}")
            name = _resolve_mt5_symbol(mt5, spec)
            if name is None:
                missing.append(spec.key)
                _report(progress, spec.key, spec.label, "missing", "Symbol not found in MT5")
                continue
            rates = mt5.copy_rates_from_pos(name, getattr(mt5, f"TIMEFRAME_{timeframe}"), 1, BARS)
            count = 0 if rates is None else len(rates)
            if count < 2:
                missing.append(spec.key)
                _report(progress, spec.key, spec.label, "missing", f"Only {count:,} candles available; at least 2 are needed for returns")
                continue
            frame = pd.DataFrame(rates)
            frame["time"] = pd.to_datetime(frame["time"], unit="s")
            frames[spec.key] = frame.set_index("time")[["open", "high", "low", "close"]].astype(float)
            _report(progress, spec.key, spec.label, "done", f"{count:,} / {BARS:,} candles downloaded from {name}")
    finally:
        mt5.shutdown()

    if len(frames) < 4:
        return MarketSnapshot(source="unavailable", error=f"At least 4 symbols with 2 or more {timeframe} candles are needed; unavailable: {', '.join(missing)}", missing=missing)
    return MarketSnapshot(source="mt5", frames=frames, missing=missing)


def save_to_excel(snapshot: MarketSnapshot, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for key, frame in snapshot.frames.items():
            export = frame.copy()
            export.index.name = "time"
            export.reset_index().to_excel(writer, sheet_name=key, index=False)
    return path


def load_from_excel(path):
    if not os.path.isfile(path):
        return MarketSnapshot(source="unavailable", error="MT5 Excel file was not created")
    try:
        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
        frames = {}
        for spec in SYMBOLS:
            frame = sheets.get(spec.key)
            if frame is None:
                continue
            if len(frame) < 2 or not {"time", "open", "high", "low", "close"}.issubset(frame.columns):
                return MarketSnapshot(source="unavailable", error=f"Excel has insufficient or invalid candles for {spec.key}")
            frame["time"] = pd.to_datetime(frame["time"])
            frames[spec.key] = frame.set_index("time")[["open", "high", "low", "close"]].astype(float)
        if len(frames) < 4:
            return MarketSnapshot(source="unavailable", error="Excel needs at least 4 symbols with usable candles")
        return MarketSnapshot(source="mt5-excel", frames=frames, missing=[key for key in ALL_KEYS if key not in frames])
    except (OSError, ValueError, KeyError) as exc:
        return MarketSnapshot(source="unavailable", error=f"Could not read MT5 Excel data: {exc}")


def load_market(profile="short", progress=None) -> MarketSnapshot:
    snapshot = fetch_from_mt5(profile, progress)
    if snapshot.error:
        return snapshot
    path = os.path.join(EXCEL_DIR, f"mt5_market_data_{TIMEFRAMES[profile][0].lower()}.xlsx")
    try:
        _report(progress, "excel", "Excel workbook", "loading", f"Saving up to {BARS:,} {TIMEFRAMES[profile][0]} candles per available symbol")
        save_to_excel(snapshot, path)
        saved = load_from_excel(path)
        if saved.error:
            _report(progress, "excel", "Excel workbook", "error", saved.error)
            return saved
        _report(progress, "excel", "Excel workbook", "done", f"Saved and verified {os.path.relpath(path, os.path.dirname(__file__))}")
        return saved
    except (OSError, ValueError) as exc:
        error = f"Could not save MT5 Excel data: {exc}"
        _report(progress, "excel", "Excel workbook", "error", error)
        return MarketSnapshot(source="unavailable", error=error)
