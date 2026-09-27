"""Faithful Python port of the Pine Script signal_logic() / vol_stop() / regime_contribution()
functions from the Global Macro Regime indicator. Operates on lists of OHLC dicts (one per
series, already aligned onto a common master calendar via align.py) and returns per-bar
ready/bull/bear flags.

Defaults mirror the original script's inputs exactly:
  signal_type='Trend', cross_type='EMA', fast=30, slow=60, cross_margin=0.3,
  vol_type='ATR', vol_input=10, vol_factor=3.0, neutral_active=True
"""


def sma(values, length):
    out = [None] * len(values)
    window = []
    s = 0.0
    for i, v in enumerate(values):
        if v is None:
            window = []
            s = 0.0
            continue
        window.append(v)
        s += v
        if len(window) > length:
            s -= window.pop(0)
        if len(window) == length:
            out[i] = s / length
    return out


def ema(values, length):
    alpha = 2.0 / (length + 1)
    out = [None] * len(values)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            prev = None
            continue
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def wma(values, length):
    out = [None] * len(values)
    n = len(values)
    denom = length * (length + 1) / 2.0
    for i in range(n):
        window = values[i - length + 1:i + 1] if i - length + 1 >= 0 else None
        if window is None or any(x is None for x in window):
            continue
        s = sum((k + 1) * window[k] for k in range(length))
        out[i] = s / denom
    return out


def rma(values, length):
    """Wilder's smoothing, matching Pine's ta.rma: seeds with an SMA(length) then recurses."""
    out = [None] * len(values)
    seeded = None
    window = []
    s = 0.0
    for i, v in enumerate(values):
        if v is None:
            window = []
            s = 0.0
            seeded = None
            continue
        if seeded is None:
            window.append(v)
            s += v
            if len(window) > length:
                s -= window.pop(0)
            if len(window) == length:
                seeded = s / length
                out[i] = seeded
            continue
        seeded = seeded + (v - seeded) / length
        out[i] = seeded
    return out


def ma(values, length, kind):
    if kind == "SMA":
        return sma(values, length)
    if kind == "WMA":
        return wma(values, length)
    if kind == "RMA":
        return rma(values, length)
    return ema(values, length)


def true_range(ohlc):
    out = [None] * len(ohlc)
    prev_close = None
    for i, row in enumerate(ohlc):
        if row is None:
            prev_close = None
            continue
        h, l, c = row["high"], row["low"], row["close"]
        if prev_close is None:
            out[i] = h - l
        else:
            out[i] = max(h - l, abs(h - prev_close), abs(l - prev_close))
        prev_close = c
    return out


def atr(ohlc, length):
    tr = true_range(ohlc)
    return rma(tr, length)


def stdev(values, length):
    out = [None] * len(values)
    window = []
    for i, v in enumerate(values):
        if v is None:
            window = []
            continue
        window.append(v)
        if len(window) > length:
            window.pop(0)
        if len(window) == length:
            m = sum(window) / length
            var = sum((x - m) ** 2 for x in window) / length
            out[i] = var ** 0.5
    return out


def mad(values, length):
    base = sma(values, length)
    dev = [abs(v - b) if (v is not None and b is not None) else None for v, b in zip(values, base)]
    return sma(dev, length)


def vol_stop(closes, volatility, factor):
    """Chandelier-style trailing stop, matching the original vol_stop() Pine function.
    `var`-style persistence: state only updates on bars where both inputs are non-null."""
    n = len(closes)
    stops = [None] * n
    up_trends = [None] * n
    max_val = min_val = stop = None
    up_trend = True
    for i in range(n):
        src, v = closes[i], volatility[i]
        if src is not None and v is not None:
            vol_m = factor * v
            max_val = src if max_val is None else max(max_val, src)
            min_val = src if min_val is None else min(min_val, src)
            if stop is None:
                stop = src
            else:
                stop = max(stop, max_val - vol_m) if up_trend else min(stop, min_val + vol_m)
            prev_up = up_trend
            up_trend = src >= stop
            if up_trend != prev_up:
                max_val = src
                min_val = src
                stop = max_val - vol_m if up_trend else min_val + vol_m
        stops[i] = stop
        up_trends[i] = up_trend if stop is not None else None
    return stops, up_trends


def signal_logic(ohlc, signal_type="Trend", cross_type="EMA", fast_input=30, slow_input=60,
                  cross_margin=0.3, vol_type="ATR", vol_input=10, vol_factor=3.0,
                  neutral_active=True):
    """ohlc: list of {open,high,low,close} or None, one per bar on the master calendar.
    Returns (ready, bull, bear) lists, one bool (or None before warm-up) per bar."""
    closes = [r["close"] if r is not None else None for r in ohlc]
    ohlc_or_none = ohlc

    fast_ma = ma(closes, fast_input, cross_type)
    slow_ma = ma(closes, slow_input, cross_type)
    atr_value = atr(ohlc_or_none, slow_input)

    if vol_type == "SD":
        volatility = stdev(closes, vol_input)
    elif vol_type == "MAD":
        volatility = mad(closes, vol_input)
    else:
        volatility = atr(ohlc_or_none, vol_input)

    v_stop, up_trend = vol_stop(closes, volatility, vol_factor)

    n = len(closes)
    ready = [False] * n
    bull = [False] * n
    bear = [False] * n
    for i in range(n):
        diff = None
        if fast_ma[i] is not None and slow_ma[i] is not None:
            diff = fast_ma[i] - slow_ma[i]
        ready_cross = diff is not None and atr_value[i] is not None
        ready_vol = volatility[i] is not None and v_stop[i] is not None

        if signal_type == "Trend":
            is_ready = ready_cross
        elif signal_type == "Volatility":
            is_ready = ready_vol
        else:
            is_ready = ready_cross and ready_vol
        ready[i] = is_ready
        if not is_ready:
            continue

        cross_score = 0
        if diff is not None and atr_value[i] is not None:
            if diff > cross_margin * atr_value[i]:
                cross_score = 1
            elif diff < -cross_margin * atr_value[i]:
                cross_score = -1
        vol_score = 1 if up_trend[i] else -1

        if signal_type == "Trend":
            total_score = cross_score
        elif signal_type == "Volatility":
            total_score = vol_score
        else:
            total_score = cross_score + vol_score

        if neutral_active:
            bull[i] = total_score > 0
        else:
            bull[i] = total_score >= 0
        bear[i] = total_score < 0
    return ready, bull, bear


def regime_contribution(valid, bull, bear, weights):
    g, r, i_, d = weights
    bull_active = valid and bull
    bear_active = valid and bear
    if bull_active:
        return g, r, i_, d
    if bear_active:
        return 1 - g, 1 - r, 1 - i_, 1 - d
    return 0, 0, 0, 0
