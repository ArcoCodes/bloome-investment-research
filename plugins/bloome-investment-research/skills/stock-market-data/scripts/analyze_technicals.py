# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "pandas", "numpy"]
# ///
"""Unified technical analysis engine.

Computes EMA 8/13/21/55/144/169, RSI, MACD with divergence, ADX, Fibonacci,
enhanced support/resistance, volume profile, and multi-timeframe analysis
(weekly/daily/4H/hourly/15min).

All skills share this single engine — data is consistent across the board.

Usage:
    python scripts/analyze_technicals.py GOOGL [--period short|medium|long] [--force]
Output: JSON with unified technical analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from provider_runtime import require_provider_module
from technical_indicators import calculate_extended_indicators

yf = require_provider_module("technicals", "yfinance")

# ---------------------------------------------------------------------------
# Cache integration
# ---------------------------------------------------------------------------

try:
    from cache_utils import load_cache, save_cache
except ImportError:
    load_cache = lambda *a, **kw: None  # noqa: E731
    save_cache = lambda *a, **kw: None  # noqa: E731

from resample_utils import resample_hourly_to_4h

# ---------------------------------------------------------------------------
# Unified EMA periods (adopted from daily-report: better for swing trading)
# ---------------------------------------------------------------------------

EMA_PERIODS = [8, 13, 21, 55, 144, 169]

# Data fetch periods (--period flag controls how much history to fetch)
PERIOD_MAP = {
    "short": {"daily_period": "6mo", "hourly_period": "30d"},
    "medium": {"daily_period": "1y", "hourly_period": "60d"},
    "long": {"daily_period": "2y", "hourly_period": "60d"},
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _is_hk_symbol(symbol: str) -> bool:
    """Check if symbol is a Hong Kong stock."""
    return symbol.upper().endswith(".HK")


def _hk_tencent_code(symbol: str) -> str:
    """Convert yfinance HK symbol (e.g. 0700.HK) to Tencent code (hk00700)."""
    code = symbol.upper().replace(".HK", "").zfill(5)
    return f"hk{code}"


def _fetch_tencent_kline(symbol: str, ktype: str = "day", count: int = 300) -> pd.DataFrame:
    """Fetch daily/weekly K-line from Tencent Finance for HK stocks.

    Args:
        symbol: yfinance-style symbol, e.g. "0700.HK"
        ktype: "day" or "week"
        count: number of bars to fetch (max ~1000)

    Returns:
        DataFrame with Open/High/Low/Close/Volume columns, DatetimeIndex.
    """
    tc = _hk_tencent_code(symbol)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={tc},{ktype},,,{count},qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return pd.DataFrame()

    # Navigate response: data -> data -> {tc} -> {ktype or "qfq"+ktype}
    inner = data.get("data", {}).get(tc, {})
    rows = inner.get(f"qfq{ktype}", inner.get(ktype, []))
    if not rows:
        return pd.DataFrame()

    # Each row: [date, open, close, high, low, volume]
    records = []
    for r in rows:
        if len(r) < 6:
            continue
        records.append({
            "Date": r[0],
            "Open": float(r[1]),
            "Close": float(r[2]),
            "High": float(r[3]),
            "Low": float(r[4]),
            "Volume": int(float(r[5])) if r[5] else 0,
        })
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    df.attrs.update({
        "source": "Tencent Finance public K-line endpoint",
        "provider_type": "undocumented public endpoint",
        "source_url": url,
        "adjustment": "qfq",
        "exchange_timezone": "Asia/Hong_Kong",
    })
    return df


def _fetch_yfinance_candles(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch adjusted OHLCV directly from Yahoo Finance via yfinance."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)
    if df is not None and not df.empty:
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.attrs.update({
            "source": "Yahoo Finance via yfinance",
            "provider_type": "unofficial wrapper/public endpoint",
            "source_url": "https://finance.yahoo.com/",
            "adjustment": "auto_adjust=True",
            "exchange_timezone": "Asia/Hong_Kong" if _is_hk_symbol(symbol) else "America/New_York",
        })
    return df if df is not None else pd.DataFrame()


def fetch_candles(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV candles. HK daily/weekly uses Tencent primary, yfinance fallback."""
    # HK stocks: use Tencent for daily/weekly (better coverage for new listings)
    if _is_hk_symbol(symbol) and interval in ("1d", "1wk"):
        ktype = "day" if interval == "1d" else "week"
        # Map period to count: "6mo"->130, "1y"->260, "2y"->520
        count_map = {"6mo": 150, "1y": 300, "2y": 520, "5y": 1300}
        count = count_map.get(period, 300)
        df = _fetch_tencent_kline(symbol, ktype=ktype, count=count)
        if not df.empty and len(df) >= 5:
            return df
        # Tencent failed, fall through to yfinance

    return _fetch_yfinance_candles(symbol, period, interval)


def frame_provenance(df: pd.DataFrame, interval: str, *, note: str | None = None) -> dict:
    """Return auditable source and coverage metadata for an OHLCV frame."""
    if df is None or df.empty:
        return {"interval": interval, "status": "unavailable", "bars": 0}
    last = pd.Timestamp(df.index[-1])
    first = pd.Timestamp(df.index[0])
    out = {
        "interval": interval,
        "status": "available",
        "source": df.attrs.get("source", "unknown"),
        "provider_type": df.attrs.get("provider_type", "unknown"),
        "source_url": df.attrs.get("source_url"),
        "adjustment": df.attrs.get("adjustment", "unknown"),
        "exchange_timezone": df.attrs.get("exchange_timezone"),
        "bars": int(len(df)),
        "first_bar": first.isoformat(),
        "last_bar": last.isoformat(),
    }
    if df.attrs.get("transformation"):
        out["transformation"] = df.attrs["transformation"]
    if note:
        out["note"] = note
    return out


def daily_bar_may_be_partial(df: pd.DataFrame, symbol: str) -> bool:
    """Flag an in-session current-day daily bar whose volume is incomplete."""
    if df is None or df.empty:
        return False
    tz_name = "Asia/Hong_Kong" if _is_hk_symbol(symbol) else "America/New_York"
    now_local = datetime.now(ZoneInfo(tz_name))
    last_date = pd.Timestamp(df.index[-1]).date()
    market_close_minutes = 16 * 60 + 10
    now_minutes = now_local.hour * 60 + now_local.minute
    return last_date == now_local.date() and now_minutes < market_close_minutes


HK_DAILY_CROSS_SOURCE_THRESHOLDS = {
    "minimum_common_sessions_fail": 5,
    "target_common_sessions": 20,
    "median_close_diff_warning_pct": 0.20,
    "median_close_diff_fail_pct": 0.50,
    "max_close_diff_warning_pct": 0.50,
    "max_close_diff_fail_pct": 1.00,
    "return_correlation_warning_below": 0.995,
    "return_correlation_fail_below": 0.980,
    "median_volume_diff_warning_pct": 10.0,
}


def assess_hk_daily_cross_source_metrics(metrics: dict) -> tuple[str, list[str]]:
    """Classify Tencent/Yahoo daily agreement using explicit quality thresholds."""
    t = HK_DAILY_CROSS_SOURCE_THRESHOLDS
    common = int(metrics.get("common_sessions", 0))
    median_close = metrics.get("median_abs_close_diff_pct")
    max_close = metrics.get("max_abs_close_diff_pct")
    return_corr = metrics.get("daily_return_correlation")
    median_volume = metrics.get("median_abs_volume_diff_pct")

    fail_reasons = []
    warning_reasons = []
    if common < t["minimum_common_sessions_fail"]:
        fail_reasons.append(f"only {common} common completed sessions")
    if median_close is not None and median_close > t["median_close_diff_fail_pct"]:
        fail_reasons.append(f"median close difference {median_close:.3f}%")
    if max_close is not None and max_close > t["max_close_diff_fail_pct"]:
        fail_reasons.append(f"maximum close difference {max_close:.3f}%")
    if return_corr is not None and return_corr < t["return_correlation_fail_below"]:
        fail_reasons.append(f"daily-return correlation {return_corr:.5f}")
    if fail_reasons:
        return "fail", fail_reasons

    if common < t["target_common_sessions"]:
        warning_reasons.append(f"only {common} of target {t['target_common_sessions']} common sessions")
    if median_close is not None and median_close > t["median_close_diff_warning_pct"]:
        warning_reasons.append(f"median close difference {median_close:.3f}%")
    if max_close is not None and max_close > t["max_close_diff_warning_pct"]:
        warning_reasons.append(f"maximum close difference {max_close:.3f}%")
    if return_corr is not None and return_corr < t["return_correlation_warning_below"]:
        warning_reasons.append(f"daily-return correlation {return_corr:.5f}")
    # Vendor volume definitions can differ, so volume divergence warns but never fails alone.
    if median_volume is not None and median_volume > t["median_volume_diff_warning_pct"]:
        warning_reasons.append(f"median volume difference {median_volume:.2f}%")
    return ("warning", warning_reasons) if warning_reasons else ("pass", [])


def cross_validate_hk_daily(primary_df: pd.DataFrame, symbol: str, lookback: int = 20) -> dict:
    """Compare HK Tencent qfq daily bars with Yahoo auto-adjusted daily bars."""
    if not _is_hk_symbol(symbol):
        return {"status": "not_applicable", "reason": "Cross-source daily check is currently required for HK tickers."}
    if primary_df is None or primary_df.empty:
        return {"status": "unavailable", "reason": "Primary daily series is unavailable."}
    if primary_df.attrs.get("source") != "Tencent Finance public K-line endpoint":
        return {
            "status": "unavailable",
            "reason": "Tencent primary daily series was unavailable; Yahoo fallback cannot be validated against itself.",
        }

    try:
        secondary_df = _fetch_yfinance_candles(symbol, period="3mo", interval="1d")
    except Exception as exc:
        return {"status": "unavailable", "reason": f"Yahoo comparison fetch failed: {type(exc).__name__}"}
    if secondary_df.empty:
        return {"status": "unavailable", "reason": "Yahoo comparison series is unavailable."}

    primary = primary_df.copy()
    secondary = secondary_df.copy()
    if daily_bar_may_be_partial(primary, symbol):
        primary = primary.iloc[:-1]
    if daily_bar_may_be_partial(secondary, symbol):
        secondary = secondary.iloc[:-1]

    def comparison_frame(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        out = df[["Close", "Volume"]].copy()
        out.index = pd.to_datetime(out.index).normalize()
        out = out[~out.index.duplicated(keep="last")]
        return out.rename(columns={"Close": f"{prefix}_close", "Volume": f"{prefix}_volume"})

    aligned = comparison_frame(primary, "tencent").join(
        comparison_frame(secondary, "yahoo"), how="inner"
    ).dropna(subset=["tencent_close", "yahoo_close"]).tail(lookback)

    common = len(aligned)
    if common == 0:
        metrics = {"common_sessions": 0}
    else:
        close_denominator = aligned[["tencent_close", "yahoo_close"]].abs().mean(axis=1).replace(0, np.nan)
        close_diff = (aligned["tencent_close"] - aligned["yahoo_close"]).abs() / close_denominator * 100
        valid_volume = aligned[(aligned["tencent_volume"] > 0) & (aligned["yahoo_volume"] > 0)]
        volume_diff = pd.Series(dtype=float)
        if not valid_volume.empty:
            volume_denominator = valid_volume[["tencent_volume", "yahoo_volume"]].abs().mean(axis=1).replace(0, np.nan)
            volume_diff = (valid_volume["tencent_volume"] - valid_volume["yahoo_volume"]).abs() / volume_denominator * 100
        returns = aligned[["tencent_close", "yahoo_close"]].pct_change().dropna()
        correlation = returns["tencent_close"].corr(returns["yahoo_close"]) if len(returns) >= 3 else np.nan
        metrics = {
            "common_sessions": int(common),
            "latest_common_date": pd.Timestamp(aligned.index[-1]).date().isoformat(),
            "latest_abs_close_diff_pct": round(float(close_diff.iloc[-1]), 4),
            "median_abs_close_diff_pct": round(float(close_diff.median()), 4),
            "max_abs_close_diff_pct": round(float(close_diff.max()), 4),
            "daily_return_correlation": round(float(correlation), 6) if not pd.isna(correlation) else None,
            "median_abs_volume_diff_pct": round(float(volume_diff.median()), 3) if not volume_diff.empty else None,
        }

    status, reasons = assess_hk_daily_cross_source_metrics(metrics)
    return {
        "status": status,
        "primary": frame_provenance(primary_df, "1d"),
        "comparison": frame_provenance(secondary_df, "1d"),
        "lookback_sessions": lookback,
        "metrics": metrics,
        "thresholds": HK_DAILY_CROSS_SOURCE_THRESHOLDS,
        "reasons": reasons,
        "interpretation": (
            "Close/return disagreement can fail the check. Volume disagreement is warning-only because vendor definitions may differ."
        ),
    }


# ---------------------------------------------------------------------------
# Core indicators
# ---------------------------------------------------------------------------

def calc_ema(series: pd.Series, span: int) -> pd.Series:
    """EMA using pandas ewm."""
    return series.ewm(span=span, adjust=False).mean()


def calc_ema_value(series: pd.Series, span: int) -> float | None:
    """Latest EMA value. Returns None if data insufficient."""
    ema = calc_ema(series, span)
    val = ema.iloc[-1]
    return float(val) if not pd.isna(val) else None


def calc_all_emas(close: pd.Series) -> dict[int, float]:
    """Calculate all standard EMAs, return {period: latest_value}."""
    result = {}
    for p in EMA_PERIODS:
        v = calc_ema_value(close, p)
        result[p] = round(v, 2) if v is not None else 0.0
    return result


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Smoothed RSI (alpha=1/period) with NaN-safe division."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def calc_rsi_dict(df: pd.DataFrame, period: int = 14) -> Optional[dict]:
    """RSI with series for divergence detection."""
    if len(df) < period + 10:
        return None
    close = df["Close"]
    rsi = calc_rsi(close, period)
    return {
        "rsi": round(float(rsi.iloc[-1]), 1),
        "rsi_series": rsi,
        "price_series": close,
    }


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26,
              signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal, histogram as Series."""
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd = ema_fast - ema_slow
    sig = calc_ema(macd, signal)
    hist = macd - sig
    return macd, sig, hist


def calc_macd_dict(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> Optional[dict]:
    """MACD with series for divergence detection."""
    if len(df) < slow + signal + 10:
        return None
    close = df["Close"]
    macd_line, signal_line, histogram = calc_macd(close, fast, slow, signal)
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
        "macd_series": macd_line,
        "price_series": close,
    }


# ---------------------------------------------------------------------------
# ADX calculation (Wilder's standard formula)
# ---------------------------------------------------------------------------

def calculate_adx(df: pd.DataFrame, period: int = 14) -> tuple[float | None, str]:
    """Calculate ADX. Returns (adx_value, trend: rising/falling/flat)."""
    if len(df) < period * 3:
        return None, "insufficient_data"

    high, low, close = df["High"], df["Low"], df["Close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    smooth_plus = plus_dm.ewm(alpha=1 / period, adjust=False).mean()
    smooth_minus = minus_dm.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * (smooth_plus / atr.replace(0, 1))
    minus_di = 100 * (smooth_minus / atr.replace(0, 1))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()

    current = adx.iloc[-1]
    prev = adx.iloc[-5] if len(adx) >= 5 else current

    if pd.isna(current):
        return None, "insufficient_data"

    if current > prev * 1.05:
        trend = "rising"
    elif current < prev * 0.95:
        trend = "falling"
    else:
        trend = "flat"

    return float(current), trend


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    high, low, close = df["High"], df["Low"], df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(atr) if not pd.isna(atr) else None


def calculate_atr_percentile(df: pd.DataFrame, period: int = 14) -> tuple[float | None, str]:
    """ATR percentile (0-100) and trend (expanding/contracting/stable)."""
    high, low, close = df["High"], df["Low"], df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()

    current_atr = atr_series.iloc[-1]
    lookback = min(60, len(atr_series) - 1)
    if lookback < 10:
        return None, "insufficient_data"

    historical = atr_series.iloc[-lookback:]
    percentile = float((historical < current_atr).sum() / len(historical) * 100)

    recent = atr_series.iloc[-5:].mean()
    prev = atr_series.iloc[-10:-5].mean() if len(atr_series) >= 10 else recent

    if recent > prev * 1.1:
        trend = "expanding"
    elif recent < prev * 0.9:
        trend = "contracting"
    else:
        trend = "stable"

    return percentile, trend


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------

def count_ema_crossings(df: pd.DataFrame, ema_span: int = 21, window: int = 20) -> int:
    """Count how many times price crossed the EMA in the last `window` bars."""
    ema = df["Close"].ewm(span=ema_span, adjust=False).mean()
    recent_close = df["Close"].iloc[-window:]
    recent_ema = ema.iloc[-window:]
    above = recent_close > recent_ema
    crossings = (above != above.shift()).sum()
    return int(crossings) if not pd.isna(crossings) else 0


def determine_regime(adx: float | None, atr_percentile: float | None, atr_trend: str,
                     ma_cross_count: int, ma_convergence: float) -> str:
    """Return 'trending', 'ranging', or 'transition'."""
    if adx is None:
        if ma_cross_count >= 5:
            return "ranging"
        if ma_cross_count <= 2:
            return "trending"
        return "transition"
    if adx > 30:
        return "trending"
    if atr_percentile is not None and atr_percentile < 20 and ma_convergence < 1.5 and adx < 30:
        return "transition"
    if adx > 25 and ma_cross_count <= 4:
        return "trending"
    if adx < 20:
        return "ranging"
    if adx < 25 and ma_cross_count >= 5:
        return "ranging"
    if 20 <= adx <= 25:
        return "transition"
    return "trending"


def determine_trend_direction(price: float, emas: dict[int, float], adx: float | None) -> str:
    """Determine trend direction using EMA alignment + ADX threshold."""
    e8 = emas.get(8, price)
    e21 = emas.get(21, price)
    e55 = emas.get(55, price)
    # Bullish: price > EMA8 > EMA21 > EMA55 (with ADX confirmation)
    if adx is not None and adx > 25:
        if e8 > e21 > e55 and price > e8:
            return "bullish"
        if e8 < e21 < e55 and price < e8:
            return "bearish"
    else:
        if e8 > e21 * 1.01:
            return "bullish"
        if e8 < e21 * 0.99:
            return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Fibonacci (from daily-report: 60-day swing high/low)
# ---------------------------------------------------------------------------

def calc_fibonacci(df: pd.DataFrame, lookback: int = 60) -> dict:
    """Calculate Fibonacci retracement levels from recent swing."""
    recent = df.tail(min(lookback, len(df)))
    if len(recent) < 10:
        return {}

    swing_high = float(recent["High"].max())
    swing_low = float(recent["Low"].min())
    high_idx = recent["High"].idxmax()
    low_idx = recent["Low"].idxmin()

    diff = swing_high - swing_low
    if diff < 0.01:
        return {}

    # Direction: if high came before low, it's a downtrend (retracement up)
    trend_dir = "down" if high_idx < low_idx else "up"

    levels = {}
    if trend_dir == "down":
        for pct, name in [(0.236, "23.6"), (0.382, "38.2"), (0.5, "50.0"), (0.618, "61.8"), (0.786, "78.6")]:
            levels[name] = round(swing_low + diff * pct, 2)
    else:
        for pct, name in [(0.236, "23.6"), (0.382, "38.2"), (0.5, "50.0"), (0.618, "61.8"), (0.786, "78.6")]:
            levels[name] = round(swing_high - diff * pct, 2)

    return {
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "trend_dir": trend_dir,
        "levels": levels,
    }


# ---------------------------------------------------------------------------
# Divergence detection (MACD & RSI)
# ---------------------------------------------------------------------------

def _find_peaks(series: pd.Series, window: int = 5) -> list[tuple[int, float]]:
    """Find local maxima (strict). Returns list of (index_pos, value)."""
    peaks = []
    vals = series.values
    for i in range(window, len(vals) - window):
        if (all(vals[i] > vals[i - j] for j in range(1, window + 1))
                and all(vals[i] > vals[i + j] for j in range(1, window + 1))):
            peaks.append((i, float(vals[i])))
    return peaks


def _find_troughs(series: pd.Series, window: int = 5) -> list[tuple[int, float]]:
    """Find local minima (strict). Returns list of (index_pos, value)."""
    troughs = []
    vals = series.values
    for i in range(window, len(vals) - window):
        if (all(vals[i] < vals[i - j] for j in range(1, window + 1))
                and all(vals[i] < vals[i + j] for j in range(1, window + 1))):
            troughs.append((i, float(vals[i])))
    return troughs


def detect_divergence(price_series: pd.Series, indicator_series: pd.Series,
                      lookback: int = 60, peak_window: int = 5) -> Optional[dict]:
    """Detect bullish/bearish divergence between price and indicator."""
    price = price_series.iloc[-lookback:]
    indicator = indicator_series.iloc[-lookback:]

    result = {"bearish": False, "bullish": False, "details": None}

    # Bearish: price higher high, indicator lower high
    price_peaks = _find_peaks(price, peak_window)
    ind_peaks = _find_peaks(indicator, peak_window)

    if len(price_peaks) >= 2 and len(ind_peaks) >= 2:
        pp1, pp2 = price_peaks[-2], price_peaks[-1]
        ip1 = min(ind_peaks, key=lambda x: abs(x[0] - pp1[0]))
        ip2 = min(ind_peaks, key=lambda x: abs(x[0] - pp2[0]))
        if ip1[0] != ip2[0] and ip1[0] < ip2[0]:
            if pp2[1] > pp1[1] and ip2[1] < ip1[1]:
                result["bearish"] = True
                result["details"] = (
                    f"Price high ${pp1[1]:.2f}->${pp2[1]:.2f} (higher), "
                    f"indicator {ip1[1]:.2f}->{ip2[1]:.2f} (lower)")

    # Bullish: price lower low, indicator higher low
    price_troughs = _find_troughs(price, peak_window)
    ind_troughs = _find_troughs(indicator, peak_window)

    if len(price_troughs) >= 2 and len(ind_troughs) >= 2:
        pt1, pt2 = price_troughs[-2], price_troughs[-1]
        it1 = min(ind_troughs, key=lambda x: abs(x[0] - pt1[0]))
        it2 = min(ind_troughs, key=lambda x: abs(x[0] - pt2[0]))
        if it1[0] != it2[0] and it1[0] < it2[0]:
            if pt2[1] < pt1[1] and it2[1] > it1[1]:
                result["bullish"] = True
                if result["details"]:
                    result["details"] += " | "
                else:
                    result["details"] = ""
                result["details"] += (
                    f"Price low ${pt1[1]:.2f}->${pt2[1]:.2f} (lower), "
                    f"indicator {it1[1]:.2f}->{it2[1]:.2f} (higher)")

    if not result["bearish"] and not result["bullish"]:
        return None
    return result


# ---------------------------------------------------------------------------
# Volume profile / consolidation zones (from original shared)
# ---------------------------------------------------------------------------

def find_volume_clusters(df: pd.DataFrame, num_bins: int = 80,
                         threshold: float = 1.5) -> list[dict]:
    """Find price zones with high volume concentration.

    Bin-based volume profile + candle density. A cluster qualifies only when
    both volume AND candle count exceed threshold × average.
    """
    if df is None or df.empty or len(df) < 10 or "Volume" not in df.columns:
        return []

    valid = df[df["Volume"] > 0]
    if len(valid) < 10:
        return []

    price_min = float(valid["Low"].min())
    price_max = float(valid["High"].max())
    bin_size = (price_max - price_min) / num_bins
    if bin_size <= 0:
        return []

    volume_profile = np.zeros(num_bins)
    candle_profile = np.zeros(num_bins)

    for _, row in valid.iterrows():
        low_bin = max(0, min(int((row["Low"] - price_min) / bin_size), num_bins - 1))
        high_bin = max(0, min(int((row["High"] - price_min) / bin_size), num_bins - 1))
        bins_spanned = high_bin - low_bin + 1
        vol_per_bin = row["Volume"] / bins_spanned
        volume_profile[low_bin:high_bin + 1] += vol_per_bin
        candle_profile[low_bin:high_bin + 1] += 1

    avg_vol = volume_profile.mean()
    avg_candles = candle_profile.mean()
    if avg_vol <= 0 or avg_candles <= 0:
        return []

    qualifies = (volume_profile >= avg_vol * threshold) & \
                (candle_profile >= avg_candles * threshold)

    clusters: list[dict] = []
    in_cluster = False
    cluster_start = 0

    for i in range(num_bins):
        if qualifies[i]:
            if not in_cluster:
                cluster_start = i
                in_cluster = True
        else:
            if in_cluster:
                _add_cluster(clusters, volume_profile, candle_profile,
                             cluster_start, i, price_min, bin_size, avg_vol, avg_candles)
                in_cluster = False
    if in_cluster:
        _add_cluster(clusters, volume_profile, candle_profile,
                     cluster_start, num_bins, price_min, bin_size, avg_vol, avg_candles)

    clusters.sort(key=lambda c: c["volume_ratio"] * c["candle_density"], reverse=True)
    return clusters[:5]


def _add_cluster(clusters, volume_profile, candle_profile,
                 start, end, price_min, bin_size, avg_vol, avg_candles):
    n_bins = end - start
    if n_bins < 1:
        return
    cluster_low = price_min + start * bin_size
    cluster_high = price_min + end * bin_size
    cluster_vol = float(volume_profile[start:end].sum())
    cluster_candles = float(candle_profile[start:end].mean())
    vol_ratio = round(cluster_vol / (avg_vol * n_bins), 2)
    candle_density = round(cluster_candles / avg_candles, 2)
    clusters.append({
        "low": round(cluster_low, 2),
        "high": round(cluster_high, 2),
        "mid": round((cluster_low + cluster_high) / 2, 2),
        "volume_ratio": vol_ratio,
        "candle_density": candle_density,
    })


def extract_volume_sr(clusters: list[dict], current_price: float) -> tuple[list[float], list[float]]:
    """Extract S/R from volume clusters."""
    supports, resistances = [], []
    for c in clusters:
        if current_price > c["high"]:
            supports.append(c["high"])
        elif current_price < c["low"]:
            resistances.append(c["low"])
        else:
            supports.append(c["low"])
            resistances.append(c["high"])
    return supports, resistances


# ---------------------------------------------------------------------------
# Swing point / structure analysis
# ---------------------------------------------------------------------------

def find_swing_points(df: pd.DataFrame, window: int = 5):
    """Find swing highs and lows. Returns (swing_highs, swing_lows) as (price, bar_index) tuples."""
    highs_arr = df["High"].values
    lows_arr = df["Low"].values
    n = len(df)
    swing_highs, swing_lows = [], []

    for i in range(window, n - window):
        if highs_arr[i] == max(highs_arr[i - window: i + window + 1]):
            swing_highs.append((float(highs_arr[i]), i))
        if lows_arr[i] == min(lows_arr[i - window: i + window + 1]):
            swing_lows.append((float(lows_arr[i]), i))

    return swing_highs, swing_lows


def determine_trend(swing_highs, swing_lows) -> str:
    """Determine trend from swing points."""
    if len(swing_lows) < 2 or len(swing_highs) < 2:
        return "sideways"
    recent_lows = [p for p, _ in swing_lows[-3:]]
    recent_highs = [p for p, _ in swing_highs[-3:]]
    hl = all(recent_lows[i] < recent_lows[i + 1] for i in range(len(recent_lows) - 1))
    hh = all(recent_highs[i] < recent_highs[i + 1] for i in range(len(recent_highs) - 1))
    ll = all(recent_lows[i] > recent_lows[i + 1] for i in range(len(recent_lows) - 1))
    lh = all(recent_highs[i] > recent_highs[i + 1] for i in range(len(recent_highs) - 1))
    if hl and hh:
        return "uptrend"
    if ll and lh:
        return "downtrend"
    return "sideways"


def find_sr_levels(current_price, swing_highs, swing_lows, total_bars=0):
    """S/R from swing points with recency weighting."""
    supports_raw = [(p, idx) for p, idx in swing_lows if p < current_price]
    resistances_raw = [(p, idx) for p, idx in swing_highs if p > current_price]

    if total_bars > 0:
        def support_key(item):
            price, idx = item
            recency = idx / total_bars
            return -(price + price * recency * 0.005)

        def resistance_key(item):
            price, idx = item
            recency = idx / total_bars
            return price - price * recency * 0.005

        supports_raw.sort(key=support_key)
        resistances_raw.sort(key=resistance_key)
    else:
        supports_raw.sort(key=lambda x: -x[0])
        resistances_raw.sort(key=lambda x: x[0])

    supports = [p for p, _ in supports_raw[:3]]
    resistances = [p for p, _ in resistances_raw[:3]]
    return supports, resistances


def extract_higher_lows(swing_lows):
    if not swing_lows:
        return []
    prices = [p for p, _ in swing_lows[-5:]]
    result = [prices[-1]]
    for i in range(len(prices) - 2, -1, -1):
        if prices[i] < result[-1]:
            result.append(prices[i])
        else:
            break
    return list(reversed(result))


def extract_lower_highs(swing_highs):
    if not swing_highs:
        return []
    prices = [p for p, _ in swing_highs[-5:]]
    result = [prices[-1]]
    for i in range(len(prices) - 2, -1, -1):
        if prices[i] > result[-1]:
            result.append(prices[i])
        else:
            break
    return list(reversed(result))


def check_structure_intact(trend, current_price, higher_lows, lower_highs,
                           swing_lows, swing_highs) -> bool:
    if trend == "uptrend" and higher_lows:
        return current_price >= higher_lows[-1]
    if trend == "downtrend" and lower_highs:
        return current_price <= lower_highs[-1]
    if trend == "sideways":
        if swing_lows:
            range_floor = min(p for p, _ in swing_lows[-3:])
            if current_price < range_floor:
                return False
        if swing_highs:
            range_ceiling = max(p for p, _ in swing_highs[-3:])
            if current_price > range_ceiling:
                return False
        return True
    return True


# ---------------------------------------------------------------------------
# Enhanced S/R: swing + volume profile + EMA confluence + touch count
# ---------------------------------------------------------------------------

def calc_enhanced_sr(df: pd.DataFrame, current_price: float,
                     emas: dict[int, float]) -> dict:
    """Enhanced support/resistance combining swing + volume + EMA + touch count.

    Framework from shared (swing + volume profile) enhanced with:
    1. EMA confluence zones (multiple EMAs close together = dynamic S/R)
    2. Touch count weighting (repeatedly tested levels are stronger)
    3. Recency weighting from shared
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    bucket_size = current_price * 0.005
    if bucket_size <= 0:
        return {"S1": None, "S2": None, "R1": None, "R2": None}

    def bucket_key(p):
        return round(round(p / bucket_size) * bucket_size, 2)

    scores = {}

    def add_score(p, pts):
        k = bucket_key(p)
        if k <= 0:
            return
        scores[k] = scores.get(k, 0) + pts

    # Factor 1: Swing points (from shared framework)
    swing_highs, swing_lows = find_swing_points(df, window=5)
    total_bars = len(df)
    for price_val, idx in swing_lows:
        recency = idx / total_bars if total_bars > 0 else 0.5
        add_score(price_val, 2.0 + recency * 1.0)  # newer swings score higher
    for price_val, idx in swing_highs:
        recency = idx / total_bars if total_bars > 0 else 0.5
        add_score(price_val, 2.0 + recency * 1.0)

    # Factor 2: Volume profile (from shared)
    vol_clusters = find_volume_clusters(df)
    for c in vol_clusters:
        combined = c["volume_ratio"] * c["candle_density"]
        add_score(c["low"], combined)
        add_score(c["high"], combined)
        add_score(c["mid"], combined * 0.5)

    # Factor 3: EMA confluence zones (from daily-report enhancement)
    ema_values = sorted(emas.values())
    for v in ema_values:
        if v > 0:
            add_score(v, 3)
    # Bonus for EMA clusters (multiple EMAs within 1% = strong zone)
    for i in range(len(ema_values)):
        for j in range(i + 1, len(ema_values)):
            if ema_values[j] > 0 and ema_values[i] > 0:
                spread_pct = abs(ema_values[j] - ema_values[i]) / ema_values[i] * 100
                if spread_pct < 1.0:
                    mid = (ema_values[i] + ema_values[j]) / 2
                    add_score(mid, 2.0)

    # Factor 4: Touch count (from daily-report enhancement)
    recent90 = df.tail(min(90, len(df)))
    for _, row in recent90.iterrows():
        h, l = float(row["High"]), float(row["Low"])
        k_h = bucket_key(h)
        k_l = bucket_key(l)
        if k_h in scores:
            scores[k_h] += 0.5
        if k_l in scores:
            scores[k_l] += 0.5

    # Factor 5: Recent high/low extremes with volume weighting (from daily-report)
    vol_ma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
    for lookback in [20, 60]:
        recent = df.tail(min(lookback, len(df)))
        for _, row in recent.iterrows():
            vol_ratio = float(row["Volume"]) / vol_ma if vol_ma > 0 else 1.0
            vol_bonus = min(vol_ratio, 3.0)
            add_score(float(row["High"]), 0.5 * vol_bonus)
            add_score(float(row["Low"]), 0.5 * vol_bonus)

    # Filter into supports (below price) and resistances (above price)
    supports = sorted(
        [(k, v) for k, v in scores.items() if k < current_price * 0.995],
        key=lambda x: (-x[1], -x[0]),
    )
    resists = sorted(
        [(k, v) for k, v in scores.items() if k > current_price * 1.005],
        key=lambda x: (-x[1], x[0]),
    )

    s1 = supports[0][0] if len(supports) >= 1 else round(current_price * 0.97, 2)
    s2 = supports[1][0] if len(supports) >= 2 else round(current_price * 0.94, 2)
    if s1 < s2:
        s1, s2 = s2, s1

    r1 = resists[0][0] if len(resists) >= 1 else round(current_price * 1.03, 2)
    r2 = resists[1][0] if len(resists) >= 2 else round(current_price * 1.06, 2)
    if r1 > r2:
        r1, r2 = r2, r1

    return {"S1": s1, "S2": s2, "R1": r1, "R2": r2}


# ---------------------------------------------------------------------------
# Analyze a single timeframe (structure only, for multi-TF)
# ---------------------------------------------------------------------------

def analyze_timeframe_structure(df: pd.DataFrame, window: int = 5,
                                current_price: float = 0) -> Optional[dict]:
    """Analyze structure for a single timeframe."""
    if df is None or df.empty or len(df) < window * 2 + 1:
        return None

    atr = calculate_atr(df)
    swing_highs, swing_lows = find_swing_points(df, window)
    trend = determine_trend(swing_highs, swing_lows)
    price = current_price or float(df["Close"].iloc[-1])
    supports, resistances = find_sr_levels(price, swing_highs, swing_lows, total_bars=len(df))
    higher_lows = extract_higher_lows(swing_lows)
    lower_highs = extract_lower_highs(swing_highs)
    intact = check_structure_intact(trend, price, higher_lows, lower_highs,
                                    swing_lows, swing_highs)

    vol_clusters = find_volume_clusters(df)
    vol_supports, vol_resistances = extract_volume_sr(vol_clusters, price)

    return {
        "trend": trend,
        "structure_intact": intact,
        "higher_lows": [round(x, 2) for x in higher_lows],
        "lower_highs": [round(x, 2) for x in lower_highs],
        "supports": [round(x, 2) for x in supports],
        "resistances": [round(x, 2) for x in resistances],
        "volume_clusters": vol_clusters,
        "volume_supports": [round(x, 2) for x in vol_supports],
        "volume_resistances": [round(x, 2) for x in vol_resistances],
        "atr": round(atr, 2) if atr is not None else 0.0,
        "swing_highs": [p for p, _ in swing_highs],
        "swing_lows": [p for p, _ in swing_lows],
    }


# ---------------------------------------------------------------------------
# Unified timeframe summary (for multi_timeframe output)
# ---------------------------------------------------------------------------

def analyze_timeframe_summary(df: pd.DataFrame, current_price: float) -> Optional[dict]:
    """Compute unified indicator summary for one timeframe.

    Returns compact dict with trend, ema_desc, rsi, macd_hist, vol_ratio.
    """
    if df is None or df.empty or len(df) < 30:
        return None

    close = df["Close"]
    volume = df["Volume"]

    emas = calc_all_emas(close)
    e8, e13, e21, e55 = emas[8], emas[13], emas[21], emas[55]

    rsi = calc_rsi(close, 14)
    rsi_v = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    _, _, macd_h = calc_macd(close)
    macd_hist_v = float(macd_h.iloc[-1]) if len(macd_h) > 0 and not pd.isna(macd_h.iloc[-1]) else 0.0

    vol_ma20 = volume.rolling(20).mean()
    vol_ma20_v = float(vol_ma20.iloc[-1]) if not pd.isna(vol_ma20.iloc[-1]) and float(vol_ma20.iloc[-1]) > 0 else None
    vol_ratio = round(float(volume.iloc[-1]) / vol_ma20_v, 1) if vol_ma20_v else None

    # EMA alignment
    if current_price > e8 > e13 > e21:
        trend_dir = "bullish"
        ema_desc = "bullish alignment"
    elif current_price < e8 < e13 < e21:
        trend_dir = "bearish"
        ema_desc = "bearish alignment"
    else:
        trend_dir = "neutral"
        ema_desc = "moving averages intertwined"

    return {
        "trend": trend_dir,
        "ema_desc": ema_desc,
        "rsi": round(rsi_v, 1),
        "macd_hist": round(macd_hist_v, 4),
        "vol_ratio": vol_ratio,
    }


# resample_to_4h moved to resample_utils.resample_hourly_to_4h


# ---------------------------------------------------------------------------
# Merge key levels from multiple timeframes
# ---------------------------------------------------------------------------

def merge_levels(levels_list: list[list[float]], dedup_pct: float = 0.5) -> list[float]:
    """Merge and deduplicate price levels."""
    all_levels = []
    for levels in levels_list:
        all_levels.extend(levels)
    if not all_levels:
        return []

    all_levels.sort()
    merged = [all_levels[0]]
    for level in all_levels[1:]:
        if merged[-1] > 0 and abs(level - merged[-1]) / merged[-1] * 100 < dedup_pct:
            merged[-1] = (merged[-1] + level) / 2
        else:
            merged.append(level)
    return [round(x, 2) for x in merged]


def sort_by_distance(levels, price):
    return sorted(levels, key=lambda x: abs(x - price))


# ---------------------------------------------------------------------------
# 15-min analysis helpers
# ---------------------------------------------------------------------------

def analyze_m15_momentum(df_15m: pd.DataFrame) -> dict:
    """15-min momentum from last 4 candles with volume."""
    result = {"direction": "neutral", "volume_trend": "stable", "high_volume_move": False}
    if df_15m is None or df_15m.empty or len(df_15m) < 8:
        return result

    last_4 = df_15m["Close"].iloc[-4:].values
    up_count = sum(1 for i in range(len(last_4) - 1) if last_4[i + 1] > last_4[i])
    down_count = sum(1 for i in range(len(last_4) - 1) if last_4[i + 1] < last_4[i])

    if up_count >= 2:
        result["direction"] = "bullish"
    elif down_count >= 2:
        result["direction"] = "bearish"

    if "Volume" in df_15m.columns:
        recent_vol = df_15m["Volume"].iloc[-4:]
        prior_vol = df_15m["Volume"].iloc[-8:-4]
        avg_recent = recent_vol.mean()
        avg_prior = prior_vol.mean()

        if avg_prior > 0:
            if avg_recent > avg_prior * 1.3:
                result["volume_trend"] = "expanding"
            elif avg_recent < avg_prior * 0.7:
                result["volume_trend"] = "contracting"

        last_vol = float(df_15m["Volume"].iloc[-1])
        avg_20 = float(df_15m["Volume"].iloc[-20:].mean()) if len(df_15m) >= 20 else avg_recent
        if avg_20 > 0 and last_vol > avg_20 * 1.5:
            result["high_volume_move"] = True

    return result


def check_level_break(current_price, levels, direction, atr=0):
    """Check if price is breaking through a level."""
    if not levels:
        return False, None
    nearest = min(levels, key=lambda x: abs(x - current_price))
    distance_pct = round(abs(current_price - nearest) / nearest * 100, 2) if nearest else 0
    threshold = atr * 0.1 if atr > 0 else nearest * 0.003 if nearest else 0

    is_breaking = False
    if direction == "support" and current_price < nearest - threshold:
        is_breaking = True
    elif direction == "resistance" and current_price > nearest + threshold:
        is_breaking = True

    return is_breaking, {"level": nearest, "distance_pct": distance_pct}


def analyze_break_confirmation(df_15m, break_level, direction="long"):
    """Break confirmation using 15-min candles."""
    if df_15m is None or df_15m.empty or len(df_15m) < 4:
        return None

    current_price = float(df_15m["Close"].iloc[-1])

    if direction == "long":
        is_broken = current_price < break_level
        distance_pct = round((break_level - current_price) / break_level * 100, 2) if break_level else 0
    else:
        is_broken = current_price > break_level
        distance_pct = round((current_price - break_level) / break_level * 100, 2) if break_level else 0

    if not is_broken and abs(distance_pct) > 1.0:
        return None

    recent = df_15m.iloc[-16:]
    closes = [float(c) for c in recent["Close"].values]
    highs = [float(h) for h in recent["High"].values]
    lows = [float(l) for l in recent["Low"].values]

    volume_confirmed = False
    break_volume_ratio = None
    if "Volume" in df_15m.columns and len(df_15m) >= 20:
        avg_vol_20 = float(df_15m["Volume"].iloc[-20:].mean())
        recent_vols = [float(v) for v in recent["Volume"].values]
        break_vol = sum(recent_vols[-4:]) / 4 if len(recent_vols) >= 4 else 0
        if avg_vol_20 > 0:
            break_volume_ratio = round(break_vol / avg_vol_20, 2)
            volume_confirmed = break_volume_ratio >= 1.5

    price_range = max(highs) - min(lows) if highs and lows else 0
    avg_price = sum(closes) / len(closes) if closes else current_price
    range_pct = (price_range / avg_price * 100) if avg_price else 0

    if len(closes) >= 4:
        last_4 = closes[-4:]
        if direction == "long":
            accelerating = all(last_4[i] >= last_4[i + 1] for i in range(len(last_4) - 1))
        else:
            accelerating = all(last_4[i] <= last_4[i + 1] for i in range(len(last_4) - 1))
    else:
        accelerating = False

    confidence_multiplier = 1.0
    if not is_broken:
        scenario = "quick_recovery" if len(closes) <= 3 else "slow_recovery"
        confidence_multiplier = 0.0 if scenario == "quick_recovery" else 0.5
    elif abs(distance_pct) > 1.5 and accelerating:
        scenario = "accelerating"
        confidence_multiplier = 1.5 if volume_confirmed else 1.3
    elif range_pct < 2.0:
        scenario = "sideways"
        confidence_multiplier = 0.7
    else:
        scenario = "pending"

    if is_broken and break_volume_ratio is not None and break_volume_ratio < 0.7:
        confidence_multiplier *= 0.6

    return {
        "break_level": break_level,
        "current_price": current_price,
        "is_broken": is_broken,
        "distance_pct": distance_pct,
        "scenario": scenario,
        "confidence_multiplier": round(confidence_multiplier, 2),
        "volume_confirmed": volume_confirmed,
        "break_volume_ratio": break_volume_ratio,
        "bars_observed": len(closes),
        "recent_closes": [round(c, 2) for c in closes[-8:]],
    }


def detect_abnormal_move(df, atr):
    """Detect abnormally large move vs ATR."""
    if df is None or df.empty or not atr or atr <= 0:
        return None
    last = df.iloc[-1]
    move = float(last["Close"] - last["Open"])
    move_abs = abs(move)
    move_pct = move / float(last["Open"]) * 100 if float(last["Open"]) > 0 else 0
    atr_multiple = move_abs / atr
    if atr_multiple >= 2.0:
        return {
            "move_pct": round(move_pct, 2),
            "atr_multiple": round(atr_multiple, 2),
            "direction": "up" if move > 0 else "down",
        }
    return None


def calculate_range_boundaries(df, lookback=20):
    recent = df.iloc[-lookback:]
    return float(recent["High"].max()), float(recent["Low"].min())


# ---------------------------------------------------------------------------
# Volume description (Chinese labels from daily-report)
# ---------------------------------------------------------------------------

def volume_desc(vol_ratio: float) -> str:
    """Chinese volume label."""
    if vol_ratio >= 2.0:
        return "high volume"
    if vol_ratio >= 1.3:
        return "rising volume"
    if vol_ratio <= 0.5:
        return "contracting volume"
    return "normal"


def analyze_price_volume_behavior(df: pd.DataFrame, symbol: str) -> dict:
    """Summarize price/volume effort versus result on the latest completed daily bar."""
    if df is None or len(df) < 22:
        return {"status": "unavailable", "reason": "fewer than 22 daily bars"}
    partial = daily_bar_may_be_partial(df, symbol)
    pos = -2 if partial and len(df) >= 23 else -1
    bar = df.iloc[pos]
    prev = df.iloc[pos - 1]
    prior = df.iloc[:pos].tail(20)
    prior_vol = prior["Volume"].replace(0, np.nan).dropna()
    avg_volume = float(prior_vol.mean()) if not prior_vol.empty else None
    volume_ratio = float(bar["Volume"]) / avg_volume if avg_volume else None
    atr_frame = df.iloc[:pos + 1] if pos != -1 else df
    atr_value = calculate_atr(atr_frame)
    bar_range = float(bar["High"] - bar["Low"])
    close_location = float((bar["Close"] - bar["Low"]) / bar_range) if bar_range > 0 else 0.5
    return_pct = float((bar["Close"] / prev["Close"] - 1) * 100) if prev["Close"] else None
    range_atr = bar_range / atr_value if atr_value else None

    if volume_ratio is None or range_atr is None or return_pct is None:
        label = "insufficient"
    elif volume_ratio >= 1.5 and range_atr < 0.75:
        label = "high_effort_low_result"
    elif volume_ratio >= 1.5 and return_pct > 0 and close_location >= 0.7:
        label = "high_effort_positive_result"
    elif volume_ratio >= 1.5 and return_pct < 0 and close_location <= 0.3:
        label = "high_effort_negative_result"
    elif volume_ratio <= 0.7 and abs(return_pct) >= 1.0:
        label = "low_participation_move"
    else:
        label = "normal_or_mixed"

    completed = df.iloc[:-1] if partial else df
    recent = completed.tail(20).copy()
    returns = recent["Close"].pct_change()
    up_volume = float(recent.loc[returns > 0, "Volume"].sum())
    down_volume = float(recent.loc[returns < 0, "Volume"].sum())
    directional_volume = up_volume + down_volume

    return {
        "status": "available",
        "bar_used": pd.Timestamp(df.index[pos]).isoformat(),
        "partial_current_daily_bar_skipped": partial,
        "return_pct": round(return_pct, 2),
        "volume_ratio_vs_prior_20": round(volume_ratio, 2) if volume_ratio is not None else None,
        "range_atr_multiple": round(range_atr, 2) if range_atr is not None else None,
        "close_location_0_to_1": round(close_location, 2),
        "effort_result": label,
        "return_20d_pct": round(float((recent["Close"].iloc[-1] / recent["Close"].iloc[0] - 1) * 100), 2),
        "up_day_volume_share_20d": round(up_volume / directional_volume, 3) if directional_volume else None,
        "method_note": "Up/down volume is classified by daily close-to-close return and is not signed order flow.",
    }


# ---------------------------------------------------------------------------
# EMA description helpers
# ---------------------------------------------------------------------------

def ema_alignment_desc(price: float, emas: dict[int, float]) -> str:
    """Chinese EMA alignment description."""
    e8, e13, e21 = emas.get(8, 0), emas.get(13, 0), emas.get(21, 0)
    if price > e8 > e13 > e21:
        return "bullish alignment"
    if price < e8 < e13 < e21:
        return "bearish alignment"
    return "moving averages intertwined"


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze(symbol: str, period: str = "medium") -> dict:
    """Run full unified technical analysis.

    Flow:
    1. Fetch ALL data: weekly, daily, hourly->4H, 15min
    2. Compute unified indicators on daily (primary)
    3. Compute multi-timeframe summaries
    4. Structure analysis (daily + 4H + 15min)
    5. Enhanced S/R, Fibonacci, divergence
    6. Merge levels, break detection
    7. Output unified structure
    """
    params = PERIOD_MAP.get(period, PERIOD_MAP["medium"])

    # --- Fetch all data ---
    daily_df = fetch_candles(symbol, params["daily_period"], "1d")
    hourly_df = fetch_candles(symbol, params["hourly_period"], "1h")
    m15_df = fetch_candles(symbol, period="5d", interval="15m")

    # Weekly data for multi-timeframe
    weekly_df = pd.DataFrame()
    try:
        weekly_df = fetch_candles(symbol, period="2y", interval="1wk")
        if weekly_df is None or weekly_df.empty:
            weekly_df = pd.DataFrame()
    except Exception:
        weekly_df = pd.DataFrame()

    if daily_df.empty or len(daily_df) < 20:
        return {"error": f"Insufficient daily data for {symbol}"}

    hk_daily_cross_validation = cross_validate_hk_daily(daily_df, symbol)
    cross_validation_status = hk_daily_cross_validation.get("status")
    if cross_validation_status == "fail":
        technical_evidence_status = "insufficient"
    elif cross_validation_status in ("warning", "unavailable"):
        technical_evidence_status = "degraded"
    else:
        technical_evidence_status = "usable"

    # Current price (15-min most up-to-date, fallback daily)
    if not m15_df.empty and len(m15_df) >= 1:
        current_price = float(m15_df["Close"].iloc[-1])
    else:
        current_price = float(daily_df["Close"].iloc[-1])

    # --- Daily primary analysis ---
    close_d = daily_df["Close"]
    volume_d = daily_df["Volume"]

    # Unified EMAs
    emas = calc_all_emas(close_d)
    ema_desc = ema_alignment_desc(current_price, emas)

    # Short track (EMA8/13) and Trend channel (EMA144/169)
    e8, e13 = emas[8], emas[13]
    e144, e169 = emas[144], emas[169]
    short_upper = max(e8, e13)
    short_lower = min(e8, e13)
    short_pos = "above" if current_price > short_upper else ("below" if current_price < short_lower else "inside_band")
    short_mid = (short_upper + short_lower) / 2
    short_dist_pct = round((current_price - short_mid) / short_mid * 100, 1) if short_mid > 0 and short_pos != "inside_band" else 0

    trend_upper = max(e144, e169)
    trend_lower = min(e144, e169)
    trend_channel_pos = "above" if current_price > trend_upper else ("below" if current_price < trend_lower else "inside_channel")
    trend_mid = (trend_upper + trend_lower) / 2
    trend_dist_pct = round((current_price - trend_mid) / trend_mid * 100, 1) if trend_mid > 0 and trend_channel_pos != "inside_channel" else 0

    # RSI
    daily_rsi = calc_rsi_dict(daily_df)
    rsi_value = daily_rsi["rsi"] if daily_rsi else None

    # MACD with divergence
    daily_macd = calc_macd_dict(daily_df)
    macd_divergence = None
    if daily_macd:
        macd_divergence = detect_divergence(
            daily_macd["price_series"], daily_macd["macd_series"],
            lookback=60, peak_window=5)

    rsi_divergence = None
    if daily_rsi:
        rsi_divergence = detect_divergence(
            daily_rsi["price_series"], daily_rsi["rsi_series"],
            lookback=60, peak_window=5)

    # ADX
    adx, adx_trend = calculate_adx(daily_df)
    atr = calculate_atr(daily_df)
    atr_pct, atr_trend = calculate_atr_percentile(daily_df)

    # Regime & trend
    ma_cross_count = count_ema_crossings(daily_df, ema_span=21)
    e21 = emas[21]
    e55 = emas[55]
    ma_convergence = abs(e21 - e55) / e55 * 100 if e55 > 0 else 0
    regime = determine_regime(adx, atr_pct, atr_trend, ma_cross_count, ma_convergence)
    trend_dir = determine_trend_direction(current_price, emas, adx)

    # Fibonacci
    fibonacci = calc_fibonacci(daily_df)

    # Enhanced S/R
    support_resist = calc_enhanced_sr(daily_df, current_price, emas)

    # Volume
    partial_daily_bar = daily_bar_may_be_partial(daily_df, symbol)
    volume_pos = -2 if partial_daily_bar and len(volume_d) >= 22 else -1
    volume_bar = float(volume_d.iloc[volume_pos])
    prior_volume = volume_d.iloc[:volume_pos].tail(20).replace(0, np.nan).dropna()
    vol_ma20_v = float(prior_volume.mean()) if not prior_volume.empty else None
    vol_ratio = round(volume_bar / vol_ma20_v, 1) if vol_ma20_v else None
    vol_desc_str = volume_desc(vol_ratio) if vol_ratio is not None else "unknown"
    price_volume_behavior = analyze_price_volume_behavior(daily_df, symbol)

    # Volume-sensitive daily indicators use the latest completed session. This
    # avoids treating an in-session partial volume bar as a completed day.
    indicator_daily_df = daily_df.iloc[:-1] if partial_daily_bar else daily_df
    extended_indicators = calculate_extended_indicators(indicator_daily_df)
    indicator_bar_used = (
        pd.Timestamp(indicator_daily_df.index[-1]).isoformat()
        if not indicator_daily_df.empty else None
    )

    # Volume clusters
    daily_vol_clusters = find_volume_clusters(daily_df)

    range_high, range_low = calculate_range_boundaries(daily_df)
    abnormal_move = detect_abnormal_move(daily_df, atr)

    # --- Structure analysis (daily / 4H / 15min) ---
    daily_struct = analyze_timeframe_structure(daily_df, window=5, current_price=current_price)

    h4_df = resample_hourly_to_4h(hourly_df, symbol=symbol)
    h4_struct = analyze_timeframe_structure(h4_df, window=3, current_price=current_price) if len(h4_df) >= 10 else None

    h4_macd = calc_macd_dict(h4_df) if len(h4_df) >= 40 else None
    h4_rsi = calc_rsi_dict(h4_df) if len(h4_df) >= 25 else None
    h4_macd_divergence = None
    if h4_macd:
        h4_macd_divergence = detect_divergence(h4_macd["price_series"], h4_macd["macd_series"], lookback=40, peak_window=3)
    h4_rsi_divergence = None
    if h4_rsi:
        h4_rsi_divergence = detect_divergence(h4_rsi["price_series"], h4_rsi["rsi_series"], lookback=40, peak_window=3)

    m15_struct = None
    if not m15_df.empty and len(m15_df) >= 10:
        m15_struct = analyze_timeframe_structure(m15_df, window=2, current_price=current_price)

    # --- Multi-timeframe summaries ---
    tf_weekly = analyze_timeframe_summary(weekly_df, current_price)
    tf_daily = analyze_timeframe_summary(daily_df, current_price)
    tf_4h = analyze_timeframe_summary(h4_df, current_price) if len(h4_df) >= 30 else None
    tf_hourly = analyze_timeframe_summary(hourly_df, current_price) if not hourly_df.empty and len(hourly_df) >= 30 else None
    tf_15min = analyze_timeframe_summary(m15_df, current_price) if not m15_df.empty and len(m15_df) >= 30 else None

    multi_timeframe = {}
    for name, tf in [("weekly", tf_weekly), ("daily", tf_daily), ("4h", tf_4h),
                     ("hourly", tf_hourly), ("15min", tf_15min)]:
        if tf:
            multi_timeframe[name] = tf

    # Resonance
    directions = [tf.get("trend", "neutral") for tf in multi_timeframe.values() if tf]
    bullish_count = sum(1 for d in directions if d == "bullish")
    bearish_count = sum(1 for d in directions if d == "bearish")
    total_tf = len(directions)
    if total_tf > 0:
        if bullish_count == total_tf:
            resonance = "all bullish"
        elif bearish_count == total_tf:
            resonance = "all bearish"
        elif bullish_count > bearish_count:
            resonance = f"{bullish_count} bullish, {bearish_count} bearish, {total_tf - bullish_count - bearish_count} neutral"
        elif bearish_count > bullish_count:
            resonance = f"{bearish_count} bearish, {bullish_count} bullish, {total_tf - bullish_count - bearish_count} neutral"
        else:
            resonance = "bullish/bearish divergence"
    else:
        resonance = "insufficient data"

    # --- Merge key levels ---
    daily_supports = daily_struct["supports"] if daily_struct else []
    daily_resistances = daily_struct["resistances"] if daily_struct else []
    h4_supports = h4_struct["supports"] if h4_struct else []
    h4_resistances = h4_struct["resistances"] if h4_struct else []
    m15_supports = m15_struct["supports"] if m15_struct else []
    m15_resistances = m15_struct["resistances"] if m15_struct else []

    daily_vol_supports = daily_struct["volume_supports"] if daily_struct else []
    daily_vol_resistances = daily_struct["volume_resistances"] if daily_struct else []
    h4_vol_supports = h4_struct["volume_supports"] if h4_struct else []
    h4_vol_resistances = h4_struct["volume_resistances"] if h4_struct else []

    m15_vol_clusters = []
    m15_vol_supports = []
    m15_vol_resistances = []
    if not m15_df.empty and len(m15_df) >= 20:
        m15_vol_clusters = find_volume_clusters(m15_df)
        m15_vol_supports, m15_vol_resistances = extract_volume_sr(m15_vol_clusters, current_price)

    key_supports = merge_levels([daily_supports, h4_supports, m15_supports,
                                  daily_vol_supports, h4_vol_supports, m15_vol_supports])
    key_resistances = merge_levels([daily_resistances, h4_resistances, m15_resistances,
                                     daily_vol_resistances, h4_vol_resistances, m15_vol_resistances])

    key_supports = sort_by_distance(key_supports, current_price)
    key_resistances = sort_by_distance(key_resistances, current_price)

    nearest_support = key_supports[0] if key_supports else None
    nearest_resistance = key_resistances[0] if key_resistances else None

    support_distance_pct = round(((current_price - nearest_support) / nearest_support) * 100, 2) if nearest_support and current_price > 0 else None
    resistance_distance_pct = round(((nearest_resistance - current_price) / current_price) * 100, 2) if nearest_resistance and current_price > 0 else None

    # --- 15min break detection ---
    m15_momentum = analyze_m15_momentum(m15_df)
    daily_atr = daily_struct["atr"] if daily_struct else 0
    breaking_support, near_support_info = check_level_break(current_price, key_supports, "support", atr=daily_atr)
    breaking_resistance, near_resistance_info = check_level_break(current_price, key_resistances, "resistance", atr=daily_atr)

    recent_closes = []
    if not m15_df.empty and len(m15_df) >= 4:
        recent_closes = [round(float(c), 2) for c in m15_df["Close"].iloc[-4:].values]

    m15_ema_fast = m15_ema_slow = m15_ema_cross = None
    if not m15_df.empty and len(m15_df) >= 20:
        _fast = calc_ema_value(m15_df["Close"], 8)
        _slow = calc_ema_value(m15_df["Close"], 21)
        m15_ema_fast = round(_fast, 2) if _fast is not None else None
        m15_ema_slow = round(_slow, 2) if _slow is not None else None
        if m15_ema_fast is not None and m15_ema_slow is not None:
            if current_price > m15_ema_fast > m15_ema_slow:
                m15_ema_cross = "bullish"
            elif current_price < m15_ema_fast < m15_ema_slow:
                m15_ema_cross = "bearish"
        else:
            m15_ema_cross = "neutral"

    break_confirmation = None
    if breaking_support and nearest_support:
        break_confirmation = analyze_break_confirmation(m15_df, nearest_support, "long")
    elif breaking_resistance and nearest_resistance:
        break_confirmation = analyze_break_confirmation(m15_df, nearest_resistance, "short")

    # --- Observations ---
    observations = []

    if cross_validation_status == "fail":
        reasons = "; ".join(hk_daily_cross_validation.get("reasons", []))
        observations.append(f"Daily cross-source validation failed; technical evidence is insufficient: {reasons}")
    elif cross_validation_status == "warning":
        reasons = "; ".join(hk_daily_cross_validation.get("reasons", []))
        observations.append(f"Daily cross-source validation warning; reduce evidence weight: {reasons}")
    elif cross_validation_status == "unavailable" and _is_hk_symbol(symbol):
        observations.append("Daily cross-source validation unavailable; reduce evidence weight and disclose the missing independent check")

    if m15_struct and not m15_struct["structure_intact"]:
        if m15_struct["trend"] == "uptrend" and m15_struct["higher_lows"]:
            observations.append(f"15min broke HL at ${m15_struct['higher_lows'][-1]}")
        elif m15_struct["trend"] == "downtrend" and m15_struct["lower_highs"]:
            observations.append(f"15min broke above LH at ${m15_struct['lower_highs'][-1]}")
        else:
            observations.append("15min structure broken")

    if breaking_support:
        observations.append(f"15min breaking support at ${nearest_support}")
    if breaking_resistance:
        observations.append(f"15min breaking resistance at ${nearest_resistance}")

    m15_dir = m15_momentum["direction"]
    m15_vol = m15_momentum["volume_trend"]
    m15_hv = m15_momentum["high_volume_move"]
    if m15_dir == "bearish":
        observations.append("15min bearish on expanding volume" if (m15_vol == "expanding" or m15_hv) else "15min momentum bearish")
    elif m15_dir == "bullish":
        observations.append("15min bullish on expanding volume" if (m15_vol == "expanding" or m15_hv) else "15min momentum bullish")

    if m15_ema_cross == "bearish" and m15_ema_fast is not None:
        observations.append(f"Price below 15min EMA8({m15_ema_fast})/EMA21({m15_ema_slow})")
    elif m15_ema_cross == "bullish" and m15_ema_fast is not None:
        observations.append(f"Price above 15min EMA8({m15_ema_fast})/EMA21({m15_ema_slow})")

    if h4_struct and not h4_struct["structure_intact"]:
        if h4_struct["trend"] == "uptrend" and h4_struct["higher_lows"]:
            observations.append(f"4H broke HL at ${h4_struct['higher_lows'][-1]}")
        elif h4_struct["trend"] == "downtrend" and h4_struct["lower_highs"]:
            observations.append(f"4H broke above LH at ${h4_struct['lower_highs'][-1]}")
        elif h4_struct["trend"] == "sideways":
            observations.append("4H broke out of sideways range")

    if daily_struct and daily_struct["structure_intact"]:
        if daily_struct["trend"] == "uptrend" and daily_struct["higher_lows"]:
            observations.append(f"Daily HL intact at ${daily_struct['higher_lows'][-1]}")
        elif daily_struct["trend"] == "downtrend" and daily_struct["lower_highs"]:
            observations.append(f"Daily LH intact at ${daily_struct['lower_highs'][-1]}")
    elif daily_struct and not daily_struct["structure_intact"]:
        if daily_struct["trend"] == "uptrend" and daily_struct["higher_lows"]:
            observations.append(f"Daily broke HL at ${daily_struct['higher_lows'][-1]}")
        elif daily_struct["trend"] == "downtrend" and daily_struct["lower_highs"]:
            observations.append(f"Daily broke above LH at ${daily_struct['lower_highs'][-1]}")
        else:
            observations.append("Daily structure broken")

    if adx is not None and adx > 40:
        observations.append(f"Strong trend momentum (ADX {adx:.0f})")
    if atr_pct is not None and atr_pct < 20:
        observations.append(f"Volatility contracting (ATR pct {atr_pct:.0f})")
    if ma_convergence < 1.5:
        observations.append(f"MAs converging ({ma_convergence:.1f}%)")
    if abnormal_move:
        observations.append(f"Abnormal {abnormal_move['direction']} move: {abnormal_move['move_pct']}% ({abnormal_move['atr_multiple']}x ATR)")

    if macd_divergence and macd_divergence["bearish"]:
        observations.append(f"Daily MACD bearish divergence: {macd_divergence['details']}")
    if macd_divergence and macd_divergence["bullish"]:
        observations.append(f"Daily MACD bullish divergence: {macd_divergence['details']}")
    if rsi_divergence and rsi_divergence["bearish"]:
        observations.append(f"Daily RSI bearish divergence: {rsi_divergence['details']}")
    if rsi_divergence and rsi_divergence["bullish"]:
        observations.append(f"Daily RSI bullish divergence: {rsi_divergence['details']}")
    if h4_macd_divergence and h4_macd_divergence["bearish"]:
        observations.append(f"4H MACD bearish divergence: {h4_macd_divergence['details']}")
    if h4_macd_divergence and h4_macd_divergence["bullish"]:
        observations.append(f"4H MACD bullish divergence: {h4_macd_divergence['details']}")
    if h4_rsi_divergence and h4_rsi_divergence["bearish"]:
        observations.append(f"4H RSI bearish divergence: {h4_rsi_divergence['details']}")
    if h4_rsi_divergence and h4_rsi_divergence["bullish"]:
        observations.append(f"4H RSI bullish divergence: {h4_rsi_divergence['details']}")

    # Volume cluster proximity
    all_clusters = (daily_struct["volume_clusters"] if daily_struct else []) + \
                   (h4_struct["volume_clusters"] if h4_struct else []) + m15_vol_clusters
    for c in all_clusters:
        score = c["volume_ratio"] * c["candle_density"]
        if score < 3.0:
            continue
        if c["low"] <= current_price <= c["high"]:
            observations.append(f"Price inside consolidation zone ${c['low']}-${c['high']} (vol {c['volume_ratio']}x, {c['candle_density']}x density)")
        elif 0 < (current_price - c["high"]) / current_price * 100 < 2.0:
            observations.append(f"Consolidation support ${c['low']}-${c['high']} nearby")
        elif 0 < (c["low"] - current_price) / current_price * 100 < 2.0:
            observations.append(f"Consolidation resistance ${c['low']}-${c['high']} nearby")

    # --- MACD signal description ---
    macd_hist_v = daily_macd["histogram"] if daily_macd else 0
    prev_hist = None
    if daily_macd:
        _, _, hist_series = calc_macd(close_d)
        if len(hist_series) > 1:
            prev_hist = float(hist_series.iloc[-2])

    divergence_str = None
    if macd_divergence:
        if macd_divergence["bearish"]:
            divergence_str = "bearish"
        elif macd_divergence["bullish"]:
            divergence_str = "bullish"

    # --- Build unified output ---
    result = {
        "symbol": symbol,
        "price": round(current_price, 2),
        "as_of": pd.Timestamp(m15_df.index[-1] if not m15_df.empty else daily_df.index[-1]).isoformat(),
        "current_price_source": "15min" if not m15_df.empty else "daily",
        "technical_evidence_status": technical_evidence_status,
        "data_provenance": {
            "daily": frame_provenance(daily_df, "1d"),
            "weekly": frame_provenance(weekly_df, "1wk"),
            "hourly": frame_provenance(hourly_df, "1h"),
            "session_half": frame_provenance(
                h4_df,
                "session_half",
                note="Two regular-session bars per trading day; compatibility key remains h4 and this is not an exchange-native 4-hour candle.",
            ),
            "intraday_15m": frame_provenance(m15_df, "15m"),
            "last_daily_bar_may_be_partial": daily_bar_may_be_partial(daily_df, symbol),
            "mixed_provider_warning": len({
                frame.attrs.get("source")
                for frame in (daily_df, weekly_df, hourly_df, m15_df)
                if frame is not None and not frame.empty and frame.attrs.get("source")
            }) > 1,
            "cross_source_validation": hk_daily_cross_validation,
            "generated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        },

        # --- Unified indicators (consistent across all skills) ---
        "ema": {str(p): v for p, v in emas.items()},
        "ema_desc": ema_desc,
        "short_track": {"upper": round(short_upper, 2), "lower": round(short_lower, 2), "pos": short_pos, "dist_pct": short_dist_pct},
        "trend_channel": {"upper": round(trend_upper, 2), "lower": round(trend_lower, 2), "pos": trend_channel_pos, "dist_pct": trend_dist_pct},
        "rsi": rsi_value,
        "macd": {
            "line": daily_macd["macd"] if daily_macd else None,
            "signal": daily_macd["signal"] if daily_macd else None,
            "histogram": daily_macd["histogram"] if daily_macd else None,
            "divergence": divergence_str,
        },
        "adx": {"value": round(adx, 1) if adx is not None else None, "trend": adx_trend},
        "regime": regime,
        "trend": trend_dir,
        "fibonacci": fibonacci,
        "support_resist": support_resist,
        "volume": {
            "ratio": vol_ratio,
            "desc": vol_desc_str,
            "bar_used": pd.Timestamp(daily_df.index[volume_pos]).isoformat(),
            "partial_current_daily_bar_skipped": partial_daily_bar,
            "clusters": daily_vol_clusters,
            "cluster_method_note": "Approximate candle-range volume distribution, not exchange volume-at-price or signed order flow.",
        },
        "price_volume_behavior": price_volume_behavior,
        "obv": extended_indicators["obv"],
        "vwap": extended_indicators["vwap"],
        "bollinger": extended_indicators["bollinger"],
        "kdj": extended_indicators["kdj"],
        "mfi": extended_indicators["mfi"],
        "extended_indicator_basis": {
            "timeframe": "1d",
            "bar_used": indicator_bar_used,
            "partial_current_daily_bar_skipped": partial_daily_bar,
            "input_adjustment": daily_df.attrs.get("adjustment", "unknown"),
            "input_source": daily_df.attrs.get("source", "unknown"),
        },
        "multi_timeframe": multi_timeframe,
        "resonance": resonance,

        # --- Detailed structure (for position-monitor / trade-setup) ---
        "m15": {
            "structure_intact": m15_struct["structure_intact"] if m15_struct else None,
            "trend": m15_struct["trend"] if m15_struct else None,
            "higher_lows": m15_struct["higher_lows"] if m15_struct else [],
            "lower_highs": m15_struct["lower_highs"] if m15_struct else [],
            "momentum": m15_momentum["direction"],
            "volume_trend": m15_momentum["volume_trend"],
            "high_volume_move": m15_momentum["high_volume_move"],
            "ema_fast": m15_ema_fast,
            "ema_slow": m15_ema_slow,
            "ema_cross": m15_ema_cross,
            "breaking_support": breaking_support,
            "breaking_resistance": breaking_resistance,
            "near_support": near_support_info,
            "near_resistance": near_resistance_info,
            "break_confirmation": break_confirmation,
            "volume_clusters": m15_vol_clusters,
            "recent_closes": recent_closes,
        },
        "h4": {
            "structure_intact": h4_struct["structure_intact"] if h4_struct else None,
            "trend": h4_struct["trend"] if h4_struct else None,
            "supports": h4_struct["supports"] if h4_struct else [],
            "resistances": h4_struct["resistances"] if h4_struct else [],
            "higher_lows": h4_struct["higher_lows"] if h4_struct else [],
            "lower_highs": h4_struct["lower_highs"] if h4_struct else [],
            "volume_clusters": h4_struct["volume_clusters"] if h4_struct else [],
        },
        "daily": {
            "structure_intact": daily_struct["structure_intact"] if daily_struct else None,
            "trend": daily_struct["trend"] if daily_struct else None,
            "supports": daily_struct["supports"] if daily_struct else [],
            "resistances": daily_struct["resistances"] if daily_struct else [],
            "higher_lows": daily_struct["higher_lows"] if daily_struct else [],
            "lower_highs": daily_struct["lower_highs"] if daily_struct else [],
            "volume_clusters": daily_struct["volume_clusters"] if daily_struct else [],
        },

        # --- Additional fields for position-monitor ---
        "adx_trend": adx_trend,
        "atr_percentile": round(atr_pct, 0) if atr_pct is not None else None,
        "atr_trend": atr_trend,
        "ma_convergence_pct": round(ma_convergence, 2),
        "ema_cross_count": ma_cross_count,
        "range_high": round(range_high, 2) if range_high else None,
        "range_low": round(range_low, 2) if range_low else None,
        "abnormal_move": abnormal_move,

        # --- Divergence detail ---
        "macd_detail": {
            "daily": {
                "value": daily_macd["macd"] if daily_macd else None,
                "signal": daily_macd["signal"] if daily_macd else None,
                "histogram": daily_macd["histogram"] if daily_macd else None,
                "divergence": {"bearish": macd_divergence["bearish"] if macd_divergence else False,
                               "bullish": macd_divergence["bullish"] if macd_divergence else False} if macd_divergence else None,
            },
            "h4": {
                "value": h4_macd["macd"] if h4_macd else None,
                "signal": h4_macd["signal"] if h4_macd else None,
                "histogram": h4_macd["histogram"] if h4_macd else None,
                "divergence": {"bearish": h4_macd_divergence["bearish"] if h4_macd_divergence else False,
                               "bullish": h4_macd_divergence["bullish"] if h4_macd_divergence else False} if h4_macd_divergence else None,
            },
        },
        "rsi_detail": {
            "daily": {
                "value": daily_rsi["rsi"] if daily_rsi else None,
                "divergence": {"bearish": rsi_divergence["bearish"] if rsi_divergence else False,
                               "bullish": rsi_divergence["bullish"] if rsi_divergence else False} if rsi_divergence else None,
            },
            "h4": {
                "value": h4_rsi["rsi"] if h4_rsi else None,
                "divergence": {"bearish": h4_rsi_divergence["bearish"] if h4_rsi_divergence else False,
                               "bullish": h4_rsi_divergence["bullish"] if h4_rsi_divergence else False} if h4_rsi_divergence else None,
            },
        },

        # --- Merged key levels ---
        "key_supports": key_supports,
        "key_resistances": key_resistances,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_distance_pct": support_distance_pct,
        "resistance_distance_pct": resistance_distance_pct,

        "key_observations": observations,
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="Unified technical analysis engine")
    parser.add_argument("symbol", help="Stock ticker symbol (e.g. GOOGL)")
    parser.add_argument("--period", choices=["short", "medium", "long"], default="medium",
                        help="Data fetch period: short (6mo), medium (1y), long (2y)")
    parser.add_argument("--force", action="store_true", help="Skip cache")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    if not args.force:
        cached = load_cache("technicals", symbol)
        if cached is not None:
            print(json.dumps(cached, indent=2))
            return

    result = analyze(symbol, args.period)
    save_cache("technicals", symbol, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
