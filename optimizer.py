"""Portfolio optimiser with selectable historical objectives and stable rebalancing.

Design notes
------------
* Objective: build the basket equity curve over the trailing window and
  score it by the selected risk or return criterion.
* No leverage: weights are long-only and sum to 1, so the basket is a genuine
  portfolio rather than a leveraged bet.
* Anti-overfit memory: the raw per-cycle optimum is blended with the previous
  published weights through an exponential moving average. Rebalances are
  additionally capped by a turnover limit, so a single noisy candle window
  cannot flip the whole allocation. This stability is exactly what keeps the
  drawdown estimate from being fitted to one particular 8,760-candle sample.
* Statistics are reported on the same trailing window used to fit the weights.
"""

import time

import numpy as np
import pandas as pd

from mt5_data import ALL_KEYS, BARS, SYMBOLS, TIMEFRAMES, MarketSnapshot

LOOKBACK = BARS
MIN_WEIGHT = 0.0
MAX_WEIGHT = 0.30
WEIGHT_STEP = 0.05
TOP_N = 5
MEMORY_ALPHA = 0.30
TURNOVER_CAP = 0.35
RF_ANNUAL = 0.04


def _returns(frames, profile="short"):
    """Align prices within the selected calendar horizon without inventing candles."""
    keys = [key for key in ALL_KEYS if key in frames and len(frames[key]) >= 2]
    if len(keys) < 4:
        return None, "At least 4 symbols with 2 or more saved candles are needed", None, None

    prices = {key: frames[key]["close"].astype(float).sort_index() for key in keys}
    start = max(series.index.min() for series in prices.values())
    end = min(series.index.max() for series in prices.values())
    if start >= end:
        return None, "The symbols have no overlapping trading dates", None, None

    common = pd.DatetimeIndex(sorted(set().union(*(series.index for series in prices.values()))))
    cutoff = end - pd.Timedelta(days=TIMEFRAMES[profile][2])
    common = common[(common >= max(start, cutoff)) & (common <= end)][-LOOKBACK:]
    aligned = pd.DataFrame({
        key: series.reindex(series.index.union(common)).sort_index().ffill().reindex(common)
        for key, series in prices.items()
    }, index=common)
    returns = np.log(aligned).diff().replace([np.inf, -np.inf], np.nan).dropna()
    if len(returns) < 2:
        return None, f"Only {len(returns):,} aligned intervals exist across available symbols; at least 2 are required", None, None
    timestamps = np.r_[0.0, (returns.index - common[0]).total_seconds().to_numpy() / 86_400.0]
    return returns[keys].to_numpy(dtype=float), "", (common[0].isoformat(), returns.index[-1].isoformat()), timestamps


def _basket_stats(returns, weights, timestamps):
    """Trailing-window statistics of the rebalanced basket."""
    daily = returns @ weights
    equity = np.r_[1.0, np.exp(np.cumsum(daily))]
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    max_dd = float(-drawdown.min())

    n = len(daily)
    years = (timestamps[-1] - timestamps[0]) / 365.25
    bars_per_year = n / years
    total = float(equity[-1] - 1.0)
    cagr = float(equity[-1] ** (1.0 / years) - 1.0)

    vol = float(daily.std(ddof=1) * np.sqrt(bars_per_year))
    mean_ann = float(daily.mean() * bars_per_year)
    sharpe = (mean_ann - RF_ANNUAL) / vol if vol > 1e-9 else 0.0
    downside = daily[daily < 0]
    sortino = (
        (mean_ann - RF_ANNUAL) / float(downside.std(ddof=1) * np.sqrt(bars_per_year))
        if len(downside) > 5 and downside.std(ddof=1) > 1e-9
        else 0.0
    )

    return {
        "total_return": total,
        "cagr": cagr,
        "volatility": vol,
        "max_drawdown": max_dd,
        "calmar": cagr / max_dd if max_dd > 1e-9 else 0.0,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "drawdown_duration_days": float(_recovery_days(drawdown, timestamps)),
        "final_equity": float(equity[-1]),
        "equity_curve": equity,
        "drawdown_curve": drawdown,
    }


def _recovery_days(drawdown, timestamps):
    """Longest elapsed time from a peak until recovery or the sample end."""
    longest = 0.0
    peak_time = timestamps[0]
    underwater = False
    for index, value in enumerate(drawdown[1:], start=1):
        if value < -1e-9:
            underwater = True
            longest = max(longest, timestamps[index] - peak_time)
        else:
            if underwater:
                longest = max(longest, timestamps[index] - peak_time)
            peak_time = timestamps[index]
            underwater = False
    return longest


OBJECTIVES = ("duration", "drawdown", "return", "sharpe")


def _score(stats, objective):
    if objective == "duration":
        return (-stats["drawdown_duration_days"], stats["total_return"])
    if objective == "drawdown":
        return (-stats["max_drawdown"], stats["total_return"])
    if objective == "return":
        return (stats["total_return"], -stats["max_drawdown"])
    return (stats["sharpe"], stats["total_return"])


def _project_simplex(vec, lower, upper):
    """Euclidean projection onto {w : sum=1, lower <= w <= upper}."""
    vec = np.clip(np.asarray(vec, dtype=float), lower, upper)
    for _ in range(60):
        diff = 1.0 - vec.sum()
        if abs(diff) < 1e-12:
            break
        if diff > 0:
            room = upper - vec
        else:
            room = vec - lower
        free = room > 1e-12
        if not free.any():
            break
        vec[free] += diff * (room[free] / room[free].sum())
        vec = np.clip(vec, lower, upper)
    if vec.sum() > 0:
        vec = vec / vec.sum()
    return vec


def _select_top(returns, keys, objective, timestamps):
    """Rank symbols by the selected objective."""
    ranked = []
    for i, key in enumerate(keys):
        single = _basket_stats(returns[:, [i]], np.array([1.0]), timestamps)
        ranked.append((_score(single, objective), i))
    ranked.sort(reverse=True)
    chosen = [i for _, i in ranked[: min(TOP_N, len(keys))]]
    if len(chosen) < 2:
        chosen = list(range(min(2, len(keys))))
    return sorted(chosen)


def _max_return_weights(returns, lower, upper):
    weights = np.full(returns.shape[1], lower)
    remaining = 1.0 - weights.sum()
    for i in np.argsort(-returns.sum(axis=0), kind="stable"):
        addition = min(upper - weights[i], remaining)
        weights[i] += addition
        remaining -= addition
        if remaining <= 1e-12:
            break
    return weights


def _round_weights(weights):
    weights = np.asarray(weights, dtype=float)
    max_units = round(MAX_WEIGHT / WEIGHT_STEP)
    units = np.floor(weights / WEIGHT_STEP + 1e-10).astype(int)
    units = np.clip(units, 0, max_units)
    remaining = round(1.0 / WEIGHT_STEP) - int(units.sum())
    while remaining:
        eligible = np.flatnonzero((weights >= WEIGHT_STEP - 1e-10) & (units < max_units))
        if not len(eligible):
            eligible = np.flatnonzero(units < max_units)
        index = eligible[np.argmax(weights[eligible] / WEIGHT_STEP - units[eligible])]
        units[index] += 1
        remaining -= 1
    return units * WEIGHT_STEP


def optimize(returns, keys, timestamps, prev_weights=None, objective="duration"):
    """One rebalance cycle: search -> shrink -> cap turnover."""
    n = len(keys)
    lower = MIN_WEIGHT
    upper = MAX_WEIGHT

    candidates = []
    eq = np.full(n, 1.0 / n)
    candidates.append(eq)
    candidates.append(_max_return_weights(returns, lower, upper))

    indices = _select_top(returns, keys, objective, timestamps)
    concentrated = np.zeros(n)
    concentrated[indices] = 1.0 / len(indices)
    candidates.append(concentrated)

    rng = np.random.default_rng(20251007)
    inv_vol = 1.0 / (returns.std(axis=0, ddof=1) + 1e-12)
    candidates.append(inv_vol / inv_vol.sum())

    for _ in range(260):
        if rng.random() < 0.5:
            raw = inv_vol * (1.0 + 0.55 * rng.standard_normal(n))
            raw = np.clip(raw, 0.0, None)
            candidates.append(_project_simplex(raw, lower, upper))
        else:
            pick = rng.choice(n, size=int(rng.integers(2, min(TOP_N, n) + 1)), replace=False)
            raw = np.zeros(n)
            raw[pick] = 1.0
            raw *= 1.0 + 0.30 * rng.standard_normal(n)
            candidates.append(_project_simplex(raw, lower, upper))

    best, best_score = eq, (float("-inf"), float("-inf"))
    for cand in candidates:
        cand = _round_weights(_project_simplex(cand, lower, upper))
        stats = _basket_stats(returns, cand, timestamps)
        score = _score(stats, objective)
        if score > best_score:
            best, best_score = cand, score

    raw_target = best

    shrunk = raw_target
    if prev_weights is not None and len(prev_weights) == n:
        prev = _round_weights(_project_simplex(prev_weights, lower, upper))
        shrunk = MEMORY_ALPHA * raw_target + (1.0 - MEMORY_ALPHA) * prev

    target, turnover = _apply_turnover_cap(shrunk, prev_weights, lower, upper)
    stats = _basket_stats(returns, target, timestamps)
    return {
        "weights": target,
        "raw_weights": raw_target,
        "turnover": turnover,
        "stats": stats,
        "score": _score(stats, objective),
    }


def _apply_turnover_cap(target, prev, lower, upper):
    target = _round_weights(_project_simplex(target, lower, upper))
    if prev is None or len(prev) != len(target):
        return target, 0.0
    prev = _round_weights(_project_simplex(prev, lower, upper))
    turnover = float(np.abs(target - prev).sum() / 2.0)
    if turnover <= TURNOVER_CAP + 1e-10:
        return target, turnover
    units = np.rint(prev / WEIGHT_STEP).astype(int)
    desired = np.rint(target / WEIGHT_STEP).astype(int)
    for _ in range(int(TURNOVER_CAP / WEIGHT_STEP + 1e-10)):
        sell = int(np.argmax(units - desired))
        buy = int(np.argmax(desired - units))
        units[sell] -= 1
        units[buy] += 1
    return units * WEIGHT_STEP, TURNOVER_CAP


def _empty_weights(count):
    return np.zeros(count)


class Engine:
    """Stateful optimiser: keeps previous weights as anti-overfit memory."""

    def __init__(self):
        self.prev_weights = None
        self.prev_stats = None
        self.prev_at = None
        self.prev_symbols = None
        self.history = []
        self.cycle = 0
        self.last_snapshot = None

    def run_cycle(self, snapshot: MarketSnapshot, objective="duration", profile="short"):
        returns, error, sample_window, timestamps = _returns(snapshot.frames, profile)
        if returns is None:
            return {"ready": False, "source": snapshot.source, "error": snapshot.error or error}
        keys = [key for key in ALL_KEYS if key in snapshot.frames and len(snapshot.frames[key]) >= 2]

        if self.prev_symbols != keys:
            self.prev_weights = None

        prev = None
        if self.prev_weights is not None and self.prev_symbols == keys:
            prev = self.prev_weights

        result = optimize(returns, keys, timestamps, prev, objective)

        weights = result["weights"]
        symbols = []
        for i, key in enumerate(keys):
            spec = next(s for s in SYMBOLS if s.key == key)
            symbols.append(
                {
                    "key": key,
                    "label": spec.label,
                    "emoji": spec.emoji,
                    "color": spec.color,
                    "weight": float(weights[i]),
                    "prev_weight": float(prev[i]) if prev is not None else None,
                    "delta": float(weights[i] - prev[i]) if prev is not None else None,
                }
            )
        symbols.sort(key=lambda s: s["weight"], reverse=True)

        rebalance = [
            {
                "key": s["key"],
                "label": s["label"],
                "emoji": s["emoji"],
                "from": s["prev_weight"],
                "to": s["weight"],
                "delta": s["delta"],
                "pct_of_prev": (s["delta"] / s["prev_weight"] * 100.0) if s["prev_weight"] and s["prev_weight"] > 1e-9 else None,
                "action": "buy" if (s["delta"] or 0) > 0.0005 else ("sell" if (s["delta"] or 0) < -0.0005 else "hold"),
            }
            for s in symbols
        ]

        stats = result["stats"]
        stats["max_return_benchmark"] = _basket_stats(returns, _max_return_weights(returns, MIN_WEIGHT, MAX_WEIGHT), timestamps)["total_return"]
        self.prev_weights = weights
        self.prev_stats = stats
        self.prev_symbols = keys
        self.prev_at = time.time()
        self.cycle += 1

        return {
            "ready": True,
            "objective": objective,
            "source": snapshot.source,
            "source_error": snapshot.error,
            "missing": snapshot.missing,
            "symbols": symbols,
            "rebalance": rebalance,
            "turnover": result["turnover"],
            "raw_weights": {k: float(w) for k, w in zip(keys, result["raw_weights"])},
            "stats": {k: v for k, v in stats.items() if not k.endswith("_curve")},
            "equity_curve": [float(x) for x in stats["equity_curve"]],
            "drawdown_curve": [float(x * 100.0) for x in stats["drawdown_curve"]],
            "bars": len(returns),
            "history_limited": bool(timestamps[-1] < TIMEFRAMES[profile][2] * 0.9),
            "sample_start": sample_window[0],
            "sample_end": sample_window[1],
            "cycle": self.cycle,
            "generated_at": time.time(),
            "unchanged": bool(prev is not None and np.abs(weights - prev).sum() < 1e-9),
        }
