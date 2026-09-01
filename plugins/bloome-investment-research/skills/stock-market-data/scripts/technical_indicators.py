"""Deterministic technical indicators derived only from normalized OHLCV bars.

The functions in this module perform no network I/O. Provider adapters must first
normalize their data to Open/High/Low/Close/Volume columns, then pass that frame
here so every market uses identical formulas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_OHLCV = ("Open", "High", "Low", "Close", "Volume")


def _unavailable(reason: str, **method: object) -> dict:
    return {"status": "unavailable", "reason": reason, **method}


def _normalized_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_OHLCV)
    missing = [column for column in REQUIRED_OHLCV if column not in df.columns]
    if missing:
        return pd.DataFrame(columns=REQUIRED_OHLCV)
    out = df.loc[:, REQUIRED_OHLCV].apply(pd.to_numeric, errors="coerce").dropna(
        subset=["High", "Low", "Close"]
    )
    out["Volume"] = out["Volume"].fillna(0).clip(lower=0)
    return out


def calculate_obv(df: pd.DataFrame, trend_window: int = 20) -> dict:
    """Calculate On-Balance Volume and a normalized least-squares trend."""
    frame = _normalized_frame(df)
    if len(frame) < 2:
        return _unavailable("fewer than 2 valid OHLCV bars", method="close direction × volume")
    if float(frame["Volume"].sum()) <= 0:
        return _unavailable("volume is zero or unavailable", method="close direction × volume")

    direction = np.sign(frame["Close"].diff()).fillna(0.0)
    obv = (direction * frame["Volume"]).cumsum()
    window = min(trend_window, len(obv))
    recent = obv.iloc[-window:].astype(float)
    slope = float(np.polyfit(np.arange(window, dtype=float), recent.to_numpy(), 1)[0]) if window >= 2 else 0.0
    average_volume = float(frame["Volume"].iloc[-window:].mean())
    normalized_slope = slope / average_volume if average_volume > 0 else 0.0
    if normalized_slope > 0.05:
        trend = "rising"
    elif normalized_slope < -0.05:
        trend = "falling"
    else:
        trend = "flat"

    price_change = float(frame["Close"].iloc[-1] / frame["Close"].iloc[-window] - 1) if window >= 2 else 0.0
    if price_change > 0 and trend == "rising" or price_change < 0 and trend == "falling":
        confirmation = "confirmed"
    elif price_change > 0 and trend == "falling":
        confirmation = "bearish_divergence_candidate"
    elif price_change < 0 and trend == "rising":
        confirmation = "bullish_divergence_candidate"
    else:
        confirmation = "mixed"

    return {
        "status": "available",
        "value": round(float(obv.iloc[-1]), 2),
        "change_5": round(float(obv.iloc[-1] - obv.iloc[-6]), 2) if len(obv) >= 6 else None,
        "trend": trend,
        "normalized_slope": round(normalized_slope, 4),
        "price_confirmation": confirmation,
        "lookback_bars": window,
        "method_note": "Starts at zero at the first supplied bar; compare direction, not absolute OBV across different history windows.",
    }


def calculate_rolling_vwap(df: pd.DataFrame, window: int = 20) -> dict:
    """Calculate rolling typical-price VWAP from bar OHLCV, not trade prints."""
    frame = _normalized_frame(df)
    if len(frame) < window:
        return _unavailable(
            f"fewer than {window} valid OHLCV bars", window=window,
            method="rolling sum(typical_price × volume) / sum(volume)",
        )
    recent = frame.iloc[-window:]
    volume_sum = float(recent["Volume"].sum())
    if volume_sum <= 0:
        return _unavailable(
            "volume is zero or unavailable", window=window,
            method="rolling sum(typical_price × volume) / sum(volume)",
        )
    typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3.0
    value = float((typical * recent["Volume"]).sum() / volume_sum)
    close = float(recent["Close"].iloc[-1])
    distance = (close / value - 1) * 100 if value else None
    return {
        "status": "available",
        "value": round(value, 4),
        "window": window,
        "price_vs_vwap_pct": round(distance, 2) if distance is not None else None,
        "position": "above" if close > value else "below" if close < value else "at",
        "price_basis": "typical_price=(high+low+close)/3",
        "method_note": "Rolling bar-derived research VWAP; not an exchange official or session trade-print VWAP.",
    }


def calculate_bollinger_bands(df: pd.DataFrame, window: int = 20, standard_deviations: float = 2.0) -> dict:
    """Calculate close-price Bollinger Bands with population standard deviation."""
    frame = _normalized_frame(df)
    if len(frame) < window:
        return _unavailable(
            f"fewer than {window} valid OHLCV bars", window=window,
            standard_deviations=standard_deviations,
        )
    close = frame["Close"].iloc[-window:].astype(float)
    middle = float(close.mean())
    std = float(close.std(ddof=0))
    upper = middle + standard_deviations * std
    lower = middle - standard_deviations * std
    latest = float(close.iloc[-1])
    width = upper - lower
    percent_b = (latest - lower) / width if width > 0 else 0.5
    if latest > upper:
        position = "above_upper"
    elif latest < lower:
        position = "below_lower"
    else:
        position = "inside"
    return {
        "status": "available",
        "middle": round(middle, 4),
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "bandwidth_pct": round(width / middle * 100, 2) if middle else None,
        "percent_b": round(percent_b, 4),
        "position": position,
        "window": window,
        "standard_deviations": standard_deviations,
        "std_ddof": 0,
    }


def calculate_kdj(df: pd.DataFrame, lookback: int = 9, smooth_k: int = 3, smooth_d: int = 3) -> dict:
    """Calculate Chinese-market KDJ using RSV(9) and 1/3 recursive smoothing."""
    frame = _normalized_frame(df)
    if len(frame) < lookback:
        return _unavailable(
            f"fewer than {lookback} valid OHLCV bars", parameters=f"{lookback},{smooth_k},{smooth_d}",
        )
    low_n = frame["Low"].rolling(lookback, min_periods=lookback).min()
    high_n = frame["High"].rolling(lookback, min_periods=lookback).max()
    price_range = high_n - low_n
    rsv = ((frame["Close"] - low_n) / price_range.replace(0, np.nan) * 100).fillna(50.0)
    k = rsv.ewm(alpha=1 / smooth_k, adjust=False).mean()
    d = k.ewm(alpha=1 / smooth_d, adjust=False).mean()
    j = 3 * k - 2 * d
    k_value, d_value, j_value = float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])
    if j_value >= 100:
        zone = "overbought"
    elif j_value <= 0:
        zone = "oversold"
    else:
        zone = "neutral"
    if len(k) >= 2 and k.iloc[-2] <= d.iloc[-2] and k_value > d_value:
        cross = "bullish_cross"
    elif len(k) >= 2 and k.iloc[-2] >= d.iloc[-2] and k_value < d_value:
        cross = "bearish_cross"
    else:
        cross = "none"
    return {
        "status": "available",
        "k": round(k_value, 2),
        "d": round(d_value, 2),
        "j": round(j_value, 2),
        "zone": zone,
        "cross": cross,
        "parameters": f"{lookback},{smooth_k},{smooth_d}",
        "method_note": "RSV with recursive smoothing alpha=1/3; J is intentionally not clipped to 0-100.",
    }


def calculate_mfi(df: pd.DataFrame, window: int = 14) -> dict:
    """Calculate Money Flow Index from typical price and bar volume."""
    frame = _normalized_frame(df)
    if len(frame) < window + 1:
        return _unavailable(
            f"fewer than {window + 1} valid OHLCV bars", window=window,
            method="typical-price money flow",
        )
    if float(frame["Volume"].sum()) <= 0:
        return _unavailable("volume is zero or unavailable", window=window, method="typical-price money flow")
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    raw_flow = typical * frame["Volume"]
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0).rolling(window).sum()
    negative = raw_flow.where(direction < 0, 0.0).rolling(window).sum()
    pos_value = float(positive.iloc[-1])
    neg_value = float(negative.iloc[-1])
    if pos_value == 0 and neg_value == 0:
        value = 50.0
    elif neg_value == 0:
        value = 100.0
    else:
        ratio = pos_value / neg_value
        value = 100.0 - 100.0 / (1.0 + ratio)
    zone = "overbought" if value >= 80 else "oversold" if value <= 20 else "neutral"
    return {
        "status": "available",
        "value": round(value, 2),
        "zone": zone,
        "window": window,
        "method_note": "Bar-derived MFI using typical price and reported volume; not signed order flow.",
    }


def calculate_extended_indicators(df: pd.DataFrame) -> dict:
    """Return the complete extended indicator bundle for one OHLCV timeframe."""
    return {
        "obv": calculate_obv(df),
        "vwap": calculate_rolling_vwap(df),
        "bollinger": calculate_bollinger_bands(df),
        "kdj": calculate_kdj(df),
        "mfi": calculate_mfi(df),
    }
