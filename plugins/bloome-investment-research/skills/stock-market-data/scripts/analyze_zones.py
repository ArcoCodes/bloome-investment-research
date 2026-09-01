# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "numpy", "pandas", "httpx"]
# ///
"""Identify entry zones + historical backtest + statistics.

Analyzes 5 years of daily OHLCV to find support / demand / MA zones,
then backtests each zone to produce win-rate, R:R, expected value, and Kelly fraction.

Usage: python scripts/analyze_zones.py --symbol AAPL [--direction long]
Output: JSON with zones array, each with backtest stats and quality rating.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import urllib.request
import numpy as np
import pandas as pd
from provider_runtime import require_provider_module

yf = require_provider_module("technicals", "yfinance")

from cache_utils import load_cache, save_cache
from resample_utils import resample_hourly_to_4h


def _fetch_tencent_daily(symbol: str, count: int = 1300) -> pd.DataFrame:
    """Fetch daily candlesticks from Tencent Finance (primary HK source, including newly listed stocks from their first trading day).
    symbol: yfinance format, for example 0100.HK
    """
    if not symbol.upper().endswith(".HK"):
        return pd.DataFrame()
    code = symbol.upper().replace(".HK", "").zfill(5)
    tc = f"hk{code}"
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={tc},day,,,{count},qfq")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return pd.DataFrame()
    inner = data.get("data", {}).get(tc, {})
    rows = inner.get("qfqday", inner.get("day", []))
    if not rows:
        return pd.DataFrame()
    records = []
    for r in rows:
        if len(r) < 6:
            continue
        records.append({"Date": r[0], "Open": float(r[1]), "Close": float(r[2]),
                         "High": float(r[3]), "Low": float(r[4]),
                         "Volume": int(float(r[5])) if r[5] else 0})
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    df.index = df.index.tz_localize("Asia/Hong_Kong")
    df.sort_index(inplace=True)
    return df

logger = logging.getLogger("zone_analysis")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ZoneCandidate:
    lower: float
    upper: float
    zone_type: str  # "support" | "demand" | "ma_support"
    timeframe: str  # "daily"
    reason: str  # e.g. "support tested 3 times" / "MA200 support zone" / "high-volume reversal demand zone"


@dataclass
class BacktestResult:
    zone: ZoneCandidate
    total_visits: int = 0
    win_count: int = 0
    loss_count: int = 0
    no_result_count: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_hold_days: float = 0.0  # average bars held for decided trades
    avg_win_hold_days: float = 0.0  # average bars held for winning trades

    @property
    def decided_count(self) -> int:
        return self.win_count + self.loss_count

    @property
    def win_rate(self) -> float:
        if self.decided_count == 0:
            return 0.0
        return self.win_count / self.decided_count

    @property
    def risk_reward(self) -> float:
        if self.avg_loss_pct == 0:
            return 0.0
        return self.avg_win_pct / self.avg_loss_pct

    @property
    def expected_value(self) -> float:
        if self.decided_count == 0:
            return 0.0
        wr = self.win_rate
        return wr * self.avg_win_pct - (1 - wr) * self.avg_loss_pct

    @property
    def kelly_fraction(self) -> float:
        rr = self.risk_reward
        if rr <= 0:
            return 0.0
        wr = self.win_rate
        k = (wr * rr - (1 - wr)) / rr
        return max(0.0, k)

    @property
    def confidence(self) -> str:
        """Statistical confidence based on sample size.

        Returns: high / medium / low / insufficient
        """
        n = self.decided_count
        if n >= 30:
            return "high"
        if n >= 15:
            return "medium"
        if n >= 5:
            return "low"
        return "insufficient"

    @property
    def quality_label(self) -> str:
        if self.confidence == "insufficient":
            return "Insufficient sample; reference only"
        if self.expected_value <= 0:
            return "Negative expected value; do not use"
        if (self.decided_count >= 15 and self.win_rate >= 0.60
                and self.expected_value > 1.0 and self.risk_reward >= 1.5):
            return "High-quality entry zone"
        if (self.decided_count >= 5 and self.win_rate >= 0.50
                and self.expected_value > 0 and self.risk_reward >= 1.0):
            return "Usable entry zone"
        if self.expected_value > 0:
            return "Marginally positive expected value; use cautiously"
        return "Negative expected value; do not use"

    @property
    def quality_emoji(self) -> str:
        label = self.quality_label
        if label == "High-quality entry zone":
            return "✅"
        if "Worth monitoring" in label:
            return "⚠️"
        if "Insufficient sample" in label:
            return "📊"
        return "⛔"


@dataclass
class ZoneAnalysis:
    symbol: str
    current_price: float
    trend: str  # uptrend / downtrend / sideways
    regime: str  # trending / ranging / transition
    zones: List[BacktestResult] = field(default_factory=list)
    data_range: str = ""
    bars_analyzed: int = 0


# ---------------------------------------------------------------------------
# Technical helpers (self-contained, no TwelveData dependency)
# ---------------------------------------------------------------------------
def _calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
              period: int = 14) -> np.ndarray:
    """Calculate ATR array. Returns array same length as input (NaN-padded)."""
    tr = np.maximum(
        highs - lows,
        np.maximum(
            np.abs(highs - np.roll(closes, 1)),
            np.abs(lows - np.roll(closes, 1)),
        ),
    )
    tr[0] = highs[0] - lows[0]
    atr = np.full_like(tr, np.nan)
    if len(tr) < period:
        return atr
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _calc_sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average with NaN padding."""
    sma = np.full_like(data, np.nan)
    if len(data) < period:
        return sma
    cumsum = np.cumsum(data)
    sma[period - 1:] = (cumsum[period - 1:] - np.concatenate([[0], cumsum[:-period]])) / period
    return sma


def _find_swing_lows(lows: np.ndarray, window: int = 5) -> List[int]:
    """Find swing low indices using N-bar window."""
    indices = []
    half = window // 2
    for i in range(half, len(lows) - half):
        if lows[i] == np.min(lows[i - half:i + half + 1]):
            indices.append(i)
    return indices


def _find_swing_highs(highs: np.ndarray, window: int = 5) -> List[int]:
    """Find swing high indices using N-bar window."""
    indices = []
    half = window // 2
    for i in range(half, len(highs) - half):
        if highs[i] == np.max(highs[i - half:i + half + 1]):
            indices.append(i)
    return indices


def _detect_trend(closes: np.ndarray, sma50: np.ndarray, sma200: np.ndarray) -> Tuple[str, str]:
    """Detect trend and regime from latest values.

    Returns (trend, regime) where:
      trend: uptrend / downtrend / sideways
      regime: trending / ranging / transition
    """
    if len(closes) < 200:
        # Not enough data for full analysis, use shorter lookback
        if len(closes) < 50:
            return "sideways", "ranging"
        cur = closes[-1]
        ma50 = sma50[-1] if not np.isnan(sma50[-1]) else cur
        if cur > ma50 * 1.02:
            return "uptrend", "trending"
        if cur < ma50 * 0.98:
            return "downtrend", "trending"
        return "sideways", "ranging"

    cur = closes[-1]
    ma50_val = sma50[-1]
    ma200_val = sma200[-1]

    if np.isnan(ma50_val) or np.isnan(ma200_val):
        return "sideways", "ranging"

    # Trend detection
    if ma50_val > ma200_val and cur > ma50_val:
        trend = "uptrend"
    elif ma50_val < ma200_val and cur < ma50_val:
        trend = "downtrend"
    else:
        trend = "sideways"

    # Regime detection
    ma_spread = abs(ma50_val - ma200_val) / ma200_val
    if ma_spread > 0.03 and trend != "sideways":
        regime = "trending"
    elif ma_spread < 0.01:
        regime = "ranging"
    else:
        regime = "transition"

    return trend, regime


# ---------------------------------------------------------------------------
# Zone identification
# ---------------------------------------------------------------------------
def identify_zones(
    df: pd.DataFrame,
    current_price: float,
    direction: str = "long",
) -> List[ZoneCandidate]:
    """Identify candidate entry zones from three sources, merge and filter.

    Sources:
    1. Support level clustering (swing lows)
    2. Moving average support bands (MA50, MA200)
    3. Demand zones (volume reversal patterns)
    """
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    volumes = df["Volume"].values

    atr_arr = _calc_atr(highs, lows, closes, 14)
    current_atr = atr_arr[-1] if not np.isnan(atr_arr[-1]) else (highs[-1] - lows[-1])

    sma50 = _calc_sma(closes, 50)
    sma200 = _calc_sma(closes, 200)

    zones: List[ZoneCandidate] = []

    # ---- Source 1: Support level clustering ----
    swing_low_idxs = _find_swing_lows(lows, window=5)
    if swing_low_idxs:
        swing_low_prices = [(idx, lows[idx]) for idx in swing_low_idxs]
        # Filter: only below current price (for long direction)
        if direction == "long":
            swing_low_prices = [(i, p) for i, p in swing_low_prices if p < current_price]
        else:
            swing_low_prices = [(i, p) for i, p in swing_low_prices if p > current_price]

        # Cluster swing lows within 1.5 * ATR, max cluster width = 3% of price
        max_cluster_width = current_price * 0.03
        clusters = _cluster_levels([p for _, p in swing_low_prices],
                                   current_atr * 1.5, max_cluster_width)

        for cluster in clusters:
            zone_lower = min(cluster) - 0.2 * current_atr
            zone_upper = max(cluster) + 0.2 * current_atr
            touch_count = len(cluster)
            zones.append(ZoneCandidate(
                lower=round(zone_lower, 2),
                upper=round(zone_upper, 2),
                zone_type="support",
                timeframe="daily",
                reason=f"support tested {touch_count} times",
            ))

        # Keep nearest 5 support zones
        if direction == "long":
            zones.sort(key=lambda z: current_price - z.upper)
            zones = [z for z in zones if z.upper < current_price][:5]

    # ---- Source 2: MA support bands ----
    for period, ma_arr, label in [(50, sma50, "MA50"), (200, sma200, "MA200")]:
        if len(closes) < period:
            continue
        ma_val = ma_arr[-1]
        if np.isnan(ma_val):
            continue

        if direction == "long" and ma_val < current_price:
            ma_lower = ma_val - 0.5 * current_atr
            ma_upper = ma_val + 0.5 * current_atr
            zones.append(ZoneCandidate(
                lower=round(ma_lower, 2),
                upper=round(ma_upper, 2),
                zone_type="ma_support",
                timeframe="daily",
                reason=f"{label} support zone",
            ))
        elif direction == "short" and ma_val > current_price:
            ma_lower = ma_val - 0.5 * current_atr
            ma_upper = ma_val + 0.5 * current_atr
            zones.append(ZoneCandidate(
                lower=round(ma_lower, 2),
                upper=round(ma_upper, 2),
                zone_type="ma_support",
                timeframe="daily",
                reason=f"{label} resistance zone",
            ))

    # ---- Source 3: Demand zones (volume reversal) ----
    vol_sma20 = _calc_sma(volumes.astype(float), 20)
    for i in range(4, len(df) - 3):
        if np.isnan(vol_sma20[i]):
            continue
        # Check: 1-3 bearish bars before → bullish reversal bar with volume ≥ 1.5× avg → 3 bars up after
        reversal_bar = i
        # Reversal bar must be bullish with high volume
        if closes[reversal_bar] <= df["Open"].values[reversal_bar]:
            continue
        if volumes[reversal_bar] < 1.5 * vol_sma20[reversal_bar]:
            continue

        # At least 1 bearish bar before
        bearish_before = False
        for j in range(max(0, reversal_bar - 3), reversal_bar):
            if closes[j] < df["Open"].values[j]:
                bearish_before = True
                break
        if not bearish_before:
            continue

        # 3 bars after should mostly go up
        if reversal_bar + 3 >= len(df):
            continue
        up_count = sum(
            1 for k in range(reversal_bar + 1, min(reversal_bar + 4, len(df)))
            if closes[k] > closes[k - 1]
        )
        if up_count < 2:
            continue

        local_atr = atr_arr[reversal_bar] if not np.isnan(atr_arr[reversal_bar]) else current_atr
        zone_lower = lows[reversal_bar] - 0.1 * local_atr
        zone_upper = highs[reversal_bar]

        if direction == "long" and zone_upper < current_price:
            zones.append(ZoneCandidate(
                lower=round(zone_lower, 2),
                upper=round(zone_upper, 2),
                zone_type="demand",
                timeframe="daily",
                reason="high-volume reversal demand zone",
            ))
        elif direction == "short" and zone_lower > current_price:
            zones.append(ZoneCandidate(
                lower=round(zone_lower, 2),
                upper=round(zone_upper, 2),
                zone_type="demand",
                timeframe="daily",
                reason="high-volume reversal supply zone",
            ))

    # ---- Pre-filter all zones by width and distance ----
    pre_filtered = []
    for z in zones:
        width_pct = (z.upper - z.lower) / current_price * 100
        if width_pct > 5:
            continue
        mid = (z.lower + z.upper) / 2
        dist_pct = abs(current_price - mid) / current_price * 100
        if dist_pct > 20:
            continue
        pre_filtered.append(z)

    # ---- Deduplicate: if two zones overlap >50%, keep the one with more info ----
    # Sort by zone_type priority: support > ma_support > demand (support has clustering info)
    type_priority = {"support": 0, "ma_support": 1, "demand": 2}
    pre_filtered.sort(key=lambda z: (type_priority.get(z.zone_type, 9), z.lower))

    deduped: List[ZoneCandidate] = []
    for z in pre_filtered:
        is_dup = False
        for existing in deduped:
            overlap_lower = max(z.lower, existing.lower)
            overlap_upper = min(z.upper, existing.upper)
            if overlap_upper > overlap_lower:
                overlap = overlap_upper - overlap_lower
                z_width = z.upper - z.lower
                if z_width > 0 and overlap / z_width > 0.5:
                    is_dup = True
                    break
        if not is_dup:
            deduped.append(z)

    # ---- Sort by distance to current price, keep top 6 ----
    deduped.sort(key=lambda z: abs(current_price - (z.lower + z.upper) / 2))
    return deduped[:6]


def _cluster_levels(prices: List[float], threshold: float,
                     max_width: Optional[float] = None) -> List[List[float]]:
    """Cluster nearby price levels.

    Args:
        threshold: max distance between consecutive levels to merge
        max_width: max total width of a cluster; start new cluster if exceeded
    """
    if not prices:
        return []
    sorted_prices = sorted(prices)
    clusters: List[List[float]] = [[sorted_prices[0]]]
    for p in sorted_prices[1:]:
        cur_cluster = clusters[-1]
        width_ok = max_width is None or (p - cur_cluster[0]) <= max_width
        if p - cur_cluster[-1] <= threshold and width_ok:
            cur_cluster.append(p)
        else:
            clusters.append([p])
    return clusters


def _merge_overlapping_zones(zones: List[ZoneCandidate]) -> List[ZoneCandidate]:
    """Merge zones with significant overlap (>30% of the smaller zone's width)."""
    if not zones:
        return []
    sorted_zones = sorted(zones, key=lambda z: z.lower)
    merged: List[ZoneCandidate] = [sorted_zones[0]]
    for z in sorted_zones[1:]:
        last = merged[-1]
        if z.lower < last.upper:
            # Calculate overlap amount vs smaller zone width
            overlap = last.upper - z.lower
            smaller_width = min(last.upper - last.lower, z.upper - z.lower)
            if smaller_width > 0 and overlap / smaller_width > 0.3:
                # Significant overlap — merge
                reasons = set()
                reasons.add(last.reason)
                reasons.add(z.reason)
                merged[-1] = ZoneCandidate(
                    lower=min(last.lower, z.lower),
                    upper=max(last.upper, z.upper),
                    zone_type=last.zone_type if last.zone_type == z.zone_type else "support",
                    timeframe="daily",
                    reason=" + ".join(sorted(reasons)),
                )
            else:
                # Minor overlap — keep separate
                merged.append(z)
        else:
            merged.append(z)
    return merged


# ---------------------------------------------------------------------------
# Historical backtest
# ---------------------------------------------------------------------------
def backtest_zone(
    zone: ZoneCandidate,
    df: pd.DataFrame,
    direction: str = "long",
    default_rr: float = 2.0,
    max_hold_bars: int = 30,
    cooldown_bars: int = 5,
) -> BacktestResult:
    """Backtest a zone against historical OHLCV data.

    For each visit to the zone:
      - Entry at zone midpoint
      - Stop loss = zone.lower - local ATR (for long)
      - Take profit = entry + (entry - stop) * default_rr, or nearest swing high if closer
      - Simulate forward max_hold_bars; first TP/SL hit wins
      - Skip cooldown bars after trade ends to avoid overlapping trades
    """
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values

    atr_arr = _calc_atr(highs, lows, closes, 14)

    # Pre-compute swing highs for resistance targets
    swing_high_idxs = _find_swing_highs(highs, window=5)

    result = BacktestResult(zone=zone)
    win_pcts: List[float] = []
    loss_pcts: List[float] = []
    hold_days: List[int] = []       # all decided trades
    win_hold_days: List[int] = []   # winning trades only

    i = 14  # Start after ATR warmup
    while i < len(df) - 1:
        # Check if bar visits the zone
        bar_low = lows[i]
        bar_high = highs[i]

        if not (bar_low <= zone.upper and bar_high >= zone.lower):
            i += 1
            continue

        # Entry at zone midpoint
        entry_price = (zone.lower + zone.upper) / 2
        local_atr = atr_arr[i] if not np.isnan(atr_arr[i]) else (bar_high - bar_low)

        if direction == "long":
            stop_loss = zone.lower - local_atr
            # Default TP based on R:R
            risk = entry_price - stop_loss
            if risk <= 0:
                i += 1
                continue
            tp_default = entry_price + risk * default_rr

            # Check for swing high resistance that's closer
            take_profit = tp_default
            for sh_idx in swing_high_idxs:
                if sh_idx < i and highs[sh_idx] > entry_price:
                    sh_price = highs[sh_idx]
                    if entry_price < sh_price < tp_default:
                        take_profit = sh_price
                        break
        else:
            stop_loss = zone.upper + local_atr
            risk = stop_loss - entry_price
            if risk <= 0:
                i += 1
                continue
            tp_default = entry_price - risk * default_rr
            take_profit = tp_default
            # For short: look for swing lows below as target
            for sl_idx in reversed(_find_swing_lows(lows[:i], window=5)):
                sl_price = lows[sl_idx]
                if sl_price < entry_price and sl_price > tp_default:
                    take_profit = sl_price
                    break

        # Simulate forward
        result.total_visits += 1
        trade_bars = 0
        trade_result = "no_result"

        for j in range(i + 1, min(i + 1 + max_hold_bars, len(df))):
            trade_bars += 1
            if direction == "long":
                if lows[j] <= stop_loss:
                    trade_result = "loss"
                    break
                if highs[j] >= take_profit:
                    trade_result = "win"
                    break
            else:
                if highs[j] >= stop_loss:
                    trade_result = "loss"
                    break
                if lows[j] <= take_profit:
                    trade_result = "win"
                    break

        if trade_result == "win":
            result.win_count += 1
            pct = abs(take_profit - entry_price) / entry_price * 100
            win_pcts.append(pct)
            hold_days.append(trade_bars)
            win_hold_days.append(trade_bars)
        elif trade_result == "loss":
            result.loss_count += 1
            pct = abs(entry_price - stop_loss) / entry_price * 100
            loss_pcts.append(pct)
            hold_days.append(trade_bars)
        else:
            result.no_result_count += 1

        # Skip trade duration + cooldown
        i += trade_bars + cooldown_bars + 1
        continue

    result.avg_win_pct = float(np.mean(win_pcts)) if win_pcts else 0.0
    result.avg_loss_pct = float(np.mean(loss_pcts)) if loss_pcts else 0.0
    result.avg_hold_days = float(np.mean(hold_days)) if hold_days else 0.0
    result.avg_win_hold_days = float(np.mean(win_hold_days)) if win_hold_days else 0.0

    return result


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------
def analyze_symbol(symbol: str, direction: str = "long") -> ZoneAnalysis:
    """Full zone analysis pipeline for a symbol.

    1. Fetch 5 years of daily OHLCV for zone identification + trend detection
    2. Fetch 2 years of 4H OHLCV for higher-resolution backtesting
    3. Detect trend / regime
    4. Identify candidate zones (from daily data)
    5. Backtest each zone (on 4H data for more samples, fallback to daily)
    6. Sort by expected value
    """
    logger.info("Fetching data for %s...", symbol)
    ticker = yf.Ticker(symbol)
    df_daily = ticker.history(period="5y", interval="1d")

    if df_daily.empty or len(df_daily) < 30:
        # Fallback: Tencent Finance for HK stocks (covers new listings from IPO day)
        logger.warning("yfinance daily insufficient for %s (%d bars), trying Tencent fallback",
                       symbol, len(df_daily))
        df_tencent = _fetch_tencent_daily(symbol)
        if not df_tencent.empty and len(df_tencent) >= 5:
            df_daily = df_tencent
            logger.info("Tencent fallback: %d daily bars for %s", len(df_daily), symbol)
        else:
            logger.error("Still insufficient data for %s after Tencent fallback", symbol)
            try:
                last_price = float(ticker.fast_info.last_price or 0)
            except Exception:
                last_price = 0
            return ZoneAnalysis(
                symbol=symbol, current_price=last_price, trend="unknown",
                regime="unknown", data_range="insufficient_data", bars_analyzed=0,
            )

    current_price = float(df_daily["Close"].iloc[-1])
    date_start = df_daily.index[0].strftime("%Y-%m-%d")
    date_end = df_daily.index[-1].strftime("%Y-%m-%d")

    # Try to fetch 4H data for higher-resolution backtesting
    # yfinance: 1h interval supports max 730 days
    df_4h = pd.DataFrame()
    try:
        df_1h = ticker.history(period="730d", interval="1h")
        if not df_1h.empty and len(df_1h) > 100:
            if df_1h.index.tz is not None:
                df_1h.index = df_1h.index.tz_localize(None)
            df_4h = resample_hourly_to_4h(df_1h, symbol=symbol)
            logger.info("Using 4H data for backtest: %d bars", len(df_4h))
        else:
            df_4h = pd.DataFrame()
    except Exception as e:
        logger.warning("Failed to fetch 4H data, falling back to daily: %s", e)
        df_4h = pd.DataFrame()

    # Use 4H for backtest if available, else daily
    df_backtest = df_4h if not df_4h.empty and len(df_4h) > 200 else df_daily
    backtest_tf = "4h" if not df_4h.empty and len(df_4h) > 200 else "daily"
    bars = len(df_backtest)

    # Trend / regime (always from daily)
    closes = df_daily["Close"].values
    sma50 = _calc_sma(closes, 50)
    sma200 = _calc_sma(closes, 200)
    trend, regime = _detect_trend(closes, sma50, sma200)

    # Identify zones (from daily data - more stable structure)
    zones = identify_zones(df_daily, current_price, direction)
    logger.info("Identified %d candidate zones for %s", len(zones), symbol)

    # Backtest each zone (on 4H if available for more samples)
    # Adjust hold/cooldown for 4H: 2 bars per trading day
    if backtest_tf == "4h":
        hold_bars = 60    # ~30 trading days × 2 bars/day
        cool_bars = 10    # ~5 trading days × 2 bars/day
    else:
        hold_bars = 30
        cool_bars = 5

    results: List[BacktestResult] = []
    for z in zones:
        bt = backtest_zone(z, df_backtest, direction,
                           max_hold_bars=hold_bars, cooldown_bars=cool_bars)
        # If using 4H, convert avg_hold bars to approximate trading days (2 bars/day)
        if backtest_tf == "4h":
            bt.avg_hold_days = bt.avg_hold_days / 2
            bt.avg_win_hold_days = bt.avg_win_hold_days / 2
        results.append(bt)
        logger.info(
            "Zone %.2f-%.2f: %d visits, %.0f%% WR, EV=%.2f%% [%s, %d bars]",
            z.lower, z.upper, bt.total_visits,
            bt.win_rate * 100, bt.expected_value,
            backtest_tf, bars,
        )

    # Sort by expected value (descending)
    results.sort(key=lambda r: r.expected_value, reverse=True)

    return ZoneAnalysis(
        symbol=symbol,
        current_price=current_price,
        trend=trend,
        regime=regime,
        zones=results,
        data_range=f"{date_start} ~ {date_end}",
        bars_analyzed=bars,
    )


# ---------------------------------------------------------------------------
# Telegram message formatting
# ---------------------------------------------------------------------------
_TREND_CN = {"uptrend": "uptrend", "downtrend": "downtrend", "sideways": "sideways"}
_REGIME_CN = {"trending": "trending", "ranging": "ranging", "transition": "transition"}


def format_zone_analysis(analysis: ZoneAnalysis) -> str:
    """Format ZoneAnalysis into a Telegram-friendly message."""
    if not analysis.zones:
        return (
            f"📊 {analysis.symbol} Entry-Zone Analysis\n\n"
            f"Current price: ${analysis.current_price:.2f}\n"
            f"No valid entry zone identified (insufficient data or no clear support)"
        )

    trend_cn = _TREND_CN.get(analysis.trend, analysis.trend)
    regime_cn = _REGIME_CN.get(analysis.regime, analysis.regime)

    lines = [
        f"📊 {analysis.symbol} Entry-Zone Analysis\n",
        f"Current price: ${analysis.current_price:.2f} | Trend: {trend_cn} | Regime: {regime_cn}",
        f"Data: {analysis.data_range} ({analysis.bars_analyzed} daily bars)\n",
    ]

    for idx, bt in enumerate(analysis.zones, 1):
        z = bt.zone
        mid = (z.lower + z.upper) / 2
        dist_pct = (analysis.current_price - mid) / analysis.current_price * 100

        lines.append(f"━━━ Zone {idx}: ${z.lower:.2f}-${z.upper:.2f} ({z.reason}) ━━━")
        lines.append(f"📍 Distance from current price: {dist_pct:+.1f}%")

        if bt.total_visits == 0:
            lines.append("🔬 Backtest: no historical sample")
        else:
            conf_label = {"high": "high confidence", "medium": "medium confidence",
                          "low": "low confidence", "insufficient": "insufficient sample"}
            lines.append(f"🔬 Backtest (sample: {bt.total_visits}, {conf_label[bt.confidence]}):")
            if bt.decided_count > 0:
                lines.append(
                    f"  Win rate: {bt.win_rate * 100:.1f}% "
                    f"({bt.win_count} wins, {bt.loss_count} losses)"
                )
                lines.append(f"  Risk/reward: {bt.risk_reward:.1f}:1")
                lines.append(f"  Expected value: {bt.expected_value:+.1f}% per trade")
                lines.append(f"  Kelly: {bt.kelly_fraction * 100:.0f}%")
                if bt.avg_win_hold_days > 0:
                    lines.append(f"  Average winning hold: {bt.avg_win_hold_days:.0f} days")
            else:
                lines.append(f"  {bt.no_result_count} observations hit neither take-profit nor stop-loss")

        lines.append(f"{bt.quality_emoji} {bt.quality_label}")
        lines.append("")

    return "\n".join(lines).rstrip()


def analysis_to_dict(analysis: ZoneAnalysis) -> dict:
    """Convert ZoneAnalysis to a JSON-serializable dict."""
    zones_data = []
    for bt in analysis.zones:
        zones_data.append({
            "lower": bt.zone.lower,
            "upper": bt.zone.upper,
            "zone_type": bt.zone.zone_type,
            "reason": bt.zone.reason,
            "total_visits": bt.total_visits,
            "win_count": bt.win_count,
            "loss_count": bt.loss_count,
            "no_result_count": bt.no_result_count,
            "win_rate": round(bt.win_rate, 4),
            "risk_reward": round(bt.risk_reward, 2),
            "expected_value": round(bt.expected_value, 4),
            "kelly_fraction": round(bt.kelly_fraction, 4),
            "avg_win_pct": round(bt.avg_win_pct, 2),
            "avg_loss_pct": round(bt.avg_loss_pct, 2),
            "avg_hold_days": round(bt.avg_hold_days, 1),
            "avg_win_hold_days": round(bt.avg_win_hold_days, 1),
            "confidence": bt.confidence,
            "quality": bt.quality_label,
        })

    return {
        "symbol": analysis.symbol,
        "current_price": analysis.current_price,
        "trend": analysis.trend,
        "regime": analysis.regime,
        "data_range": analysis.data_range,
        "bars_analyzed": analysis.bars_analyzed,
        "zones": zones_data,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Zone analysis for entry opportunities")
    parser.add_argument("--symbol", required=True, help="Ticker symbol (e.g. AAPL, 0700.HK)")
    parser.add_argument("--direction", default="long", choices=["long", "short"],
                        help="Trading direction (default: long)")
    parser.add_argument("--log-level", default="INFO",
                        help="Logging level (default: INFO)")
    parser.add_argument("--force", action="store_true", help="Skip cache")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    cache_key = f"{args.symbol}_{args.direction}"
    if not args.force:
        cached = load_cache("zones", cache_key)
        if cached is not None:
            print(json.dumps(cached, ensure_ascii=False, indent=2))
            return

    analysis = analyze_symbol(args.symbol, args.direction)
    content = format_zone_analysis(analysis)

    output = {
        "type": "zone_analysis",
        "symbol": args.symbol,
        "content": content,
        "data": analysis_to_dict(analysis),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    save_cache("zones", cache_key, output)


if __name__ == "__main__":
    main()
