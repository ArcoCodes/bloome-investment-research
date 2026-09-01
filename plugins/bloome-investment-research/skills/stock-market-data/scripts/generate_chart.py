# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "mplfinance", "matplotlib", "pandas", "numpy"]
# ///
"""Generate a research or trade-context candlestick chart with key structures.

Usage:
  python scripts/generate_chart.py --symbol NVDA --mode research --interval 1d --days 120
  python scripts/generate_chart.py --symbol NVDA --mode position --entry 170 --stop 160 --target 200
  python scripts/generate_chart.py --symbol 0700.HK --mode setup --zone-low 370 --zone-high 385 --stop 355 --target 420

Output: JSON {"path": "/tmp/chart_NVDA_xxx.png", ...}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import mplfinance as mpf
import numpy as np
import pandas as pd
import yfinance as yf


def market_currency(symbol: str) -> tuple[str, str]:
    """Return a display prefix and ISO-like quote-currency label by listing suffix."""
    normalized = symbol.upper()
    suffixes = (
        ((".HK",), ("HK$", "HKD")),
        ((".T",), ("¥", "JPY")),
        ((".SS", ".SZ"), ("¥", "CNY")),
        ((".KS", ".KQ"), ("₩", "KRW")),
        ((".L",), ("£", "GBP")),
    )
    for market_suffixes, display in suffixes:
        if normalized.endswith(market_suffixes):
            return display
    return "$", "USD"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def fetch_float_shares(symbol: str) -> float | None:
    """Fetch float shares for turnover rate (volume / float * 100).
    Returns None if data looks unreliable (daily turnover would exceed 50%).
    """
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        v = info.get("floatShares") or info.get("sharesOutstanding")
        if not v:
            return None
        float_shares = float(v)
        # Sanity check: estimate daily avg volume and expected daily turnover
        # If implied daily turnover > 50%, the float_shares value is likely wrong
        hist = ticker.history(period="5d", interval="1d")
        if not hist.empty:
            avg_daily_vol = float(hist["Volume"].mean())
            implied_turnover = avg_daily_vol / float_shares
            if implied_turnover > 0.50:   # > 50% daily turnover → bad data
                return None
        return float_shares
    except Exception:
        return None


def fetch_1h(symbol: str, trading_days: int = 7) -> pd.DataFrame:
    cal_days = int(trading_days * 1.6) + 3
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{cal_days}d", interval="1h")
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    keep = trading_days * 8
    if len(df) > keep:
        df = df.iloc[-keep:]
    return df


def fetch_3h(symbol: str, trading_days: int = 7) -> pd.DataFrame:
    """Fetch 1H data then resample to 3H candles."""
    # Fetch more 1H bars to have enough after resampling
    cal_days = int(trading_days * 1.6) + 5
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{cal_days}d", interval="1h")
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

    # Resample 1H → 3H
    df_3h = df.resample("3h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Open", "Close"])

    # Keep only candles that had trading activity
    df_3h = df_3h[df_3h["Volume"] > 0]

    # Trim to roughly trading_days worth of 3H bars
    # US: ~2 bars/day  HK: ~2 bars/day  → keep trading_days * 3 for safety
    keep = trading_days * 3
    if len(df_3h) > keep:
        df_3h = df_3h.iloc[-keep:]
    return df_3h


def fetch_daily(symbol: str, trading_days: int = 60) -> pd.DataFrame:
    """Fetch daily candles; keep the requested number of trading sessions."""
    calendar_days = max(int(trading_days * 1.7) + 10, 30)
    df = yf.Ticker(symbol).history(period=f"{calendar_days}d", interval="1d")
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df.iloc[-trading_days:]


def fetch_weekly(symbol: str, trading_weeks: int = 104) -> pd.DataFrame:
    """Aggregate daily history into completed Friday-labeled weekly bars."""
    calendar_days = max(int(trading_weeks * 7 * 1.15) + 30, 365)
    daily = yf.Ticker(symbol).history(period=f"{calendar_days}d", interval="1d")
    if daily is None or daily.empty:
        return pd.DataFrame()
    daily.index = pd.to_datetime(daily.index)
    if daily.index.tz is not None:
        daily.index = daily.index.tz_localize(None)
    daily = daily[["Open", "High", "Low", "Close", "Volume"]].dropna()
    latest_session = daily.index[-1].normalize()
    weekly = daily.resample("W-FRI").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Open", "Close"])
    if not weekly.empty and weekly.index[-1].normalize() > latest_session:
        weekly = weekly.iloc[:-1]
    return weekly.iloc[-trading_weeks:]


# ---------------------------------------------------------------------------
# S/R detection
# ---------------------------------------------------------------------------

def find_sr_levels(df: pd.DataFrame, current: float,
                   window: int = 5,
                   price_range_pct: float = 0.12) -> list[tuple[float, str]]:
    """Returns up to 4 S/R levels (≤2 S + ≤2 R), closest first."""
    highs = df["High"].values
    lows  = df["Low"].values
    raw: list[tuple[float, str]] = []

    for i in range(window, len(highs) - window):
        if highs[i] >= max(highs[i - window: i + window + 1]) * 0.9995:
            raw.append((float(highs[i]), "R"))
        if lows[i] <= min(lows[i - window: i + window + 1]) * 1.0005:
            raw.append((float(lows[i]), "S"))

    filtered = [(p, t) for p, t in raw
                if abs(p - current) / current <= price_range_pct]

    # Cluster within 0.6%
    filtered.sort(key=lambda x: x[0])
    clustered: list[tuple[float, str]] = []
    skip: set[int] = set()
    for i, (p1, _) in enumerate(filtered):
        if i in skip:
            continue
        group = [p1]
        for j in range(i + 1, len(filtered)):
            if j not in skip and abs(filtered[j][0] - p1) / p1 < 0.006:
                group.append(filtered[j][0])
                skip.add(j)
        avg = sum(group) / len(group)
        label = "S" if avg < current else "R"
        clustered.append((avg, label))

    # Sort by distance; take up to 2 S and 2 R (balanced)
    clustered.sort(key=lambda x: abs(x[0] - current))
    result: list[tuple[float, str]] = []
    counts = {"S": 0, "R": 0}
    for p, t in clustered:
        if counts[t] >= 2:
            continue
        if all(abs(p - rp) / rp >= 0.005 for rp, _ in result):
            result.append((p, t))
            counts[t] += 1
        if sum(counts.values()) >= 4:
            break
    return result


# ---------------------------------------------------------------------------
# Swing structure (HH / HL / LH / LL)
# ---------------------------------------------------------------------------

def find_swing_structure(
    df: pd.DataFrame, window: int = 4, max_swings: int = 3
) -> list[tuple[int, float, str, str]]:
    """
    Find the last `max_swings` confirmed swing points labeled HH/HL/LH/LL.
    Returns list of (bar_index, price, label, 'high'|'low'), sorted by bar_index.
    """
    highs = df["High"].values
    lows  = df["Low"].values
    n = len(highs)

    sh: list[tuple[int, float]] = []   # (index, price)
    sl: list[tuple[int, float]] = []

    for i in range(window, n - window):
        if highs[i] >= max(highs[i - window: i + window + 1]) * 0.9998:
            sh.append((i, float(highs[i])))
        if lows[i]  <= min(lows[i  - window: i + window + 1]) * 1.0002:
            sl.append((i, float(lows[i])))

    result: list[tuple[int, float, str, str]] = []
    for k in range(1, len(sh)):
        label = "HH" if sh[k][1] > sh[k - 1][1] else "LH"
        result.append((sh[k][0], sh[k][1], label, "high"))
    for k in range(1, len(sl)):
        label = "HL" if sl[k][1] > sl[k - 1][1] else "LL"
        result.append((sl[k][0], sl[k][1], label, "low"))

    result.sort(key=lambda x: x[0])
    return result[-max_swings:]


# ---------------------------------------------------------------------------
# Build chart
# ---------------------------------------------------------------------------

def build_chart(
    symbol: str, df: pd.DataFrame, mode: str,
    entry: float | None, stop: float | None, target: float | None,
    zone_low: float | None, zone_high: float | None,
    out_path: str,
    float_shares: float | None = None,
    trading_days: int = 7,
    interval: str = "1h",
) -> None:
    current = float(df["Close"].iloc[-1])

    # ── Style (white) ─────────────────────────────────────────────────────────
    # High-contrast green/red palette (TradingView style)
    _UP   = "#00c853"   # pure bright green
    _DOWN = "#f44336"   # pure bright red
    mc = mpf.make_marketcolors(
        up=_UP, down=_DOWN,
        wick={"up": _UP, "down": _DOWN},
        edge="inherit",
        volume={"up": _UP, "down": _DOWN},
    )
    # ── CJK font fallback ─────────────────────────────────────────────────────
    import matplotlib.font_manager as fm
    _CJK_CANDIDATES = [
        "PingFang SC", "Hiragino Sans GB", "Heiti SC",
        "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Noto Sans CJK TC",
        "Noto Sans CJK JP", "Source Han Sans CN",
    ]
    _cjk_font = next(
        (f for f in _CJK_CANDIDATES
         if any(f.lower() in fp.name.lower() for fp in fm.fontManager.ttflist)),
        None,
    )
    _font_family = [_cjk_font, "sans-serif"] if _cjk_font else ["sans-serif"]

    style = mpf.make_mpf_style(
        base_mpf_style="default",
        marketcolors=mc,
        facecolor="#ffffff",
        edgecolor="#dddddd",
        figcolor="#ffffff",
        gridcolor="#eeeeee",
        gridstyle="-",
        gridaxis="both",
        y_on_right=True,
        rc={
            "font.family": _font_family,
            "font.size": 9,
            "text.color": "#222222",
            "axes.labelcolor": "#444444",
            "xtick.color": "#555555",
            "ytick.color": "#555555",
            "axes.edgecolor": "#cccccc",
        },
    )

    # ── Y range: K-lines + key levels, with padding ───────────────────────────
    p_lo = float(df["Low"].min())
    p_hi = float(df["High"].max())
    # Expand to include key levels that are within 20% of the K-line range
    kline_range = p_hi - p_lo
    _key_prices = [v for v in [entry, stop, target, zone_low, zone_high] if v]
    for kp in _key_prices:
        if abs(kp - current) / current <= 0.20:
            p_lo = min(p_lo, kp)
            p_hi = max(p_hi, kp)
    margin = max((p_hi - p_lo) * 0.08, kline_range * 0.06)
    ylim_lo = p_lo - margin
    ylim_hi = p_hi + margin

    # ── Key level lines (stop/target/entry) ───────────────────────────────────
    KEY_STOP   = ("#e74c3c", 1.1, "--")   # red, thin
    KEY_TARGET = ("#2980b9", 1.1, "--")   # blue, thin
    KEY_ENTRY  = ("#00b894", 1.1, "--")   # green, thin

    key_levels: list[tuple[float, str, str, float, str]] = []  # price, label, color, lw, ls
    if mode == "position":
        if entry:  key_levels.append((entry,  f"Cost  {entry}",   *KEY_ENTRY))
        if stop:   key_levels.append((stop,   f"Stop  {stop}",   *KEY_STOP))
        if target: key_levels.append((target, f"Target  {target}", *KEY_TARGET))
    elif mode == "setup":
        if stop:   key_levels.append((stop,   f"Stop  {stop}",   *KEY_STOP))
        if target: key_levels.append((target, f"Target  {target}", *KEY_TARGET))

    visible   = [lv for lv in key_levels if ylim_lo <= lv[0] <= ylim_hi]
    offscreen = [lv for lv in key_levels if lv[0] < ylim_lo or lv[0] > ylim_hi]

    hlines_kwarg: dict = {}
    if visible:
        hlines_kwarg = dict(
            hlines=dict(
                hlines=[lv[0] for lv in visible],
                colors=[lv[2] for lv in visible],
                linestyle=[lv[4] for lv in visible],
                linewidths=[lv[3] for lv in visible],
            )
        )

    # ── EMA lines ─────────────────────────────────────────────────────────────
    close = df["Close"]
    addplot_list = []
    ema_cfg = [
        (8,  "#ff8c00", 1.4),   # vivid orange  — short term
        (21, "#f5d800", 1.4),   # bright yellow — medium term (distinct from orange)
        (55, "#9b59b6", 1.4),   # vivid purple  — long term
    ]
    for period, color, lw in ema_cfg:
        if len(close) >= period:
            ema_series = close.ewm(span=period, adjust=False).mean()
            addplot_list.append(mpf.make_addplot(ema_series, color=color, width=lw,
                                                  panel=0, alpha=0.9))

    # ── Turnover line (volume / float * 100) ──────────────────────────────────
    has_turnover = False
    if float_shares and float_shares > 0:
        turnover = df["Volume"] / float_shares * 100
        addplot_list.append(
            mpf.make_addplot(turnover, panel=1, color="#2980b9", width=1.3,
                             secondary_y=True, alpha=0.85)
        )
        has_turnover = True

    # ── Plot ──────────────────────────────────────────────────────────────────
    change_pct = (current - float(df["Close"].iloc[0])) / float(df["Close"].iloc[0]) * 100
    sign = "+" if change_pct >= 0 else ""

    fig, axes = mpf.plot(
        df, type="candle", style=style,
        title="",
        ylabel="",
        volume=True,
        figsize=(12, 7.5),
        ylim=(ylim_lo, ylim_hi),
        returnfig=True,
        tight_layout=False,
        warn_too_much_data=10000,
        panel_ratios=(3, 1),
        addplot=addplot_list if addplot_list else None,
        **hlines_kwarg,
    )
    ax = axes[0]
    # axes layout with secondary_y turnover:
    #   axes[0]=price, axes[1]=placeholder, axes[2]=volume bars, axes[3]=turnover
    # without turnover: axes[0]=price, axes[1]=volume bars
    vol_ax   = axes[2] if has_turnover else axes[1]
    turn_ax  = axes[3] if has_turnover and len(axes) > 3 else None

    # ── Title: left-aligned, plain Chinese ───────────────────────────────────
    price_prefix, currency = market_currency(symbol)
    price_str = f"{price_prefix}{current:.2f}"
    _interval_label = {
        "1h": "1-hour bars",
        "3h": "3-hour bars",
        "1d": "daily bars",
        "1wk": "weekly bars",
    }.get(interval, interval)
    _window_label = (
        f"{trading_days}-Week" if interval == "1wk"
        else f"{trading_days}-Session" if interval == "1d"
        else f"{trading_days}-Day"
    )
    latest_session = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
    ax.set_title(
        f"{symbol}   {_window_label} Price Structure ({_interval_label})   |   {latest_session}",
        fontsize=12, fontweight="bold", color="#111111",
        loc="left", pad=10,
    )

    # ── Price panel Y axis label ───────────────────────────────────────────────
    ax.set_ylabel(f"Price ({currency})", fontsize=8.5, color="#555555", labelpad=4)

    # ── Price label: bottom-left inside chart, away from right-side key labels ──
    price_color = "#26a69a" if change_pct >= 0 else "#ef5350"
    ax.annotate(
        f"{price_str}  {sign}{change_pct:.1f}%",
        xy=(0.015, 0.04), xycoords="axes fraction",
        fontsize=13, fontweight="bold", color=price_color,
        ha="left", va="bottom",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                  edgecolor=price_color, alpha=0.88, linewidth=0.8),
    )

    # ── EMA legend (top-left inside chart) ────────────────────────────────────
    from matplotlib.lines import Line2D
    legend_handles = [
        Line2D([0], [0], color="#ff8c00", linewidth=2.0, label="EMA8"),
        Line2D([0], [0], color="#f5d800", linewidth=2.0, label="EMA21"),
        Line2D([0], [0], color="#9b59b6", linewidth=2.0, label="EMA55"),
    ]
    ax.legend(handles=legend_handles, loc="upper left", fontsize=8.5,
              framealpha=0.85, edgecolor="#cccccc", handlelength=2.0)

    # ── Horizontal x-axis labels ──────────────────────────────────────────────
    # Force all axes x-tick labels horizontal (mplfinance sometimes rotates them)
    for _a in fig.axes:
        plt.setp(_a.get_xticklabels(), rotation=0, ha="center", fontsize=8.5)
    fig.autofmt_xdate(rotation=0, ha="center")

    # ── Subtle S/R lines that do not compete with the primary series ──────────
    sr = find_sr_levels(df, current)
    for sr_price, sr_type in sr:
        color = "#ef5350" if sr_type == "R" else "#26a69a"
        ax.axhline(sr_price, color=color, linewidth=0.7,
                   linestyle=(0, (5, 4)), alpha=0.4, zorder=2)
        ax.text(
            0.012, sr_price,
            f"{sr_type}  {sr_price:.1f}",
            transform=ax.get_yaxis_transform(),
            fontsize=7, color=color, va="center",
            fontweight="normal", alpha=0.7,
        )

    # ── Swing structure labels (HH / HL / LH / LL) ───────────────────────────
    _SC = {"HH": "#ff8c00", "LH": "#f44336", "HL": "#00c853", "LL": "#c62828"}
    price_range = ylim_hi - ylim_lo
    swings = find_swing_structure(df)
    for bar_idx, price, label, swing_type in swings:
        color = _SC[label]
        if swing_type == "high":
            marker_y = price + price_range * 0.012
            ax.plot(bar_idx, marker_y, marker="v", color=color,
                    markersize=5, zorder=6, clip_on=True)
            ax.text(bar_idx, marker_y + price_range * 0.015, label,
                    ha="center", va="bottom", fontsize=7.5,
                    fontweight="bold", color=color, clip_on=True)
        else:
            marker_y = price - price_range * 0.012
            ax.plot(bar_idx, marker_y, marker="^", color=color,
                    markersize=5, zorder=6, clip_on=True)
            ax.text(bar_idx, marker_y - price_range * 0.015, label,
                    ha="center", va="top", fontsize=7.5,
                    fontweight="bold", color=color, clip_on=True)

    # ── Entry zone shading (setup) ─────────────────────────────────────────────
    if mode == "setup" and zone_low and zone_high:
        ax.axhspan(zone_low, zone_high, alpha=0.08, color="#16a085", zorder=0)

    # ── Key level labels: RIGHT side, inside chart ────────────────────────────
    def _label_inside_right(ax, price, text, color):
        ax.text(
            0.988, price, text,
            transform=ax.get_yaxis_transform(),
            fontsize=8.5, color=color, va="center", ha="right",
            fontweight="bold", clip_on=True,
            bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                      edgecolor=color, alpha=0.85, linewidth=0.6),
        )

    for price, label, color, lw, ls in visible:
        _label_inside_right(ax, price, label, color)

    for price, label, color, lw, ls in offscreen:
        above = price > ylim_hi
        y_pin = ylim_hi * 0.9985 if above else ylim_lo * 1.0015
        arrow = "▲" if above else "▼"
        _label_inside_right(ax, y_pin, f"{arrow} {label}", color)

    if mode == "setup" and zone_low and zone_high:
        mid = (zone_low + zone_high) / 2
        if ylim_lo <= mid <= ylim_hi:
            _label_inside_right(ax, mid, f"Entry zone {zone_low}–{zone_high}", "#16a085")

    # ── Volume panel: visible separator from price panel ──────────────────────
    vol_ax.set_facecolor("#f7f7f7")
    ax.spines["bottom"].set_linewidth(1.5)
    ax.spines["bottom"].set_color("#aaaaaa")

    # Semi-transparent volume bars
    for patch in vol_ax.patches:
        patch.set_alpha(0.7)

    vol_ax.set_title("")
    # Left Y axis: volume in M/K
    def _fmt_vol(v, _):
        if v >= 1e6:   return f"{v/1e6:.0f}M"
        if v >= 1e3:   return f"{v/1e3:.0f}K"
        return str(int(v))
    vol_ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_vol))
    vol_ax.set_ylabel("Volume", fontsize=8, color="#555555", labelpad=3)
    vol_ax.tick_params(axis="y", labelsize=7.5)
    vol_ax.set_xlabel("")

    # Average volume reference line
    avg_vol = df["Volume"].mean()
    vol_ax.axhline(avg_vol, color="#e74c3c", linewidth=0.8, linestyle="--", alpha=0.55)
    vol_ax.text(
        0.012, avg_vol, "Average",
        transform=vol_ax.get_yaxis_transform(),
        fontsize=7, color="#e74c3c", va="bottom",
        bbox=dict(boxstyle="round,pad=0.1", facecolor="white",
                  edgecolor="#e74c3c", alpha=0.7, linewidth=0.4),
    )

    # Turnover secondary axis label
    if turn_ax is not None:
        turn_ax.set_ylabel("Turnover %", fontsize=8, color="#2980b9", labelpad=3)
        turn_ax.tick_params(axis="y", labelsize=7.5, colors="#2980b9")
        turn_ax.yaxis.label.set_color("#2980b9")

    plt.savefig(out_path, dpi=140, bbox_inches="tight",
                facecolor="#ffffff", pad_inches=0.2)
    plt.close(fig)


def write_chart_spec(
    symbol: str,
    df: pd.DataFrame,
    mode: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
    zone_low: float | None,
    zone_high: float | None,
    out_path: str,
    interval: str,
) -> str:
    """Write the structured data used by the self-contained HTML chart component."""
    current = float(df["Close"].iloc[-1])
    _, currency = market_currency(symbol)
    categories = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in df.index]
    candles = [
        {
            "open": round(float(row.Open), 4),
            "high": round(float(row.High), 4),
            "low": round(float(row.Low), 4),
            "close": round(float(row.Close), 4),
            "volume": round(float(row.Volume), 2),
        }
        for row in df.itertuples()
    ]
    overlay_colors = {8: "#ff8c00", 21: "#d9b900", 55: "#9b59b6"}
    overlays = []
    for period, color in overlay_colors.items():
        if len(df) >= period:
            values = df["Close"].ewm(span=period, adjust=False).mean()
            overlays.append(
                {
                    "name": f"EMA{period}",
                    "color": color,
                    "values": [round(float(value), 4) for value in values],
                }
            )

    levels = [
        {
            "label": level_type,
            "value": round(price, 4),
            "color": "#d85745" if level_type == "R" else "#18a779",
        }
        for price, level_type in find_sr_levels(df, current)
    ]
    named_levels = (("Cost", entry), ("Stop", stop), ("Target", target))
    if mode in {"position", "setup"}:
        for label, value in named_levels:
            if value is not None:
                levels.append({"label": label, "value": round(value, 4)})
    if mode == "setup" and zone_low is not None and zone_high is not None:
        levels.extend(
            [
                {"label": "Zone low", "value": round(zone_low, 4), "color": "#18a779"},
                {"label": "Zone high", "value": round(zone_high, 4), "color": "#18a779"},
            ]
        )

    annotations = [
        {
            "index": bar_index,
            "value": round(price, 4),
            "label": label,
            "position": "below" if swing_type == "low" else "above",
            "color": "#18a779" if swing_type == "low" else "#d85745",
        }
        for bar_index, price, label, swing_type in find_swing_structure(df)
    ]
    interval_label = {
        "1h": "1-hour bars",
        "3h": "3-hour bars",
        "1d": "daily bars",
        "1wk": "weekly bars",
    }.get(interval, interval)
    spec = {
        "type": "candlestick",
        "title": f"{symbol} · {interval_label} · {categories[-1]}",
        "aria_label": (
            f"Interactive {interval_label} price chart for {symbol} with OHLC, volume, "
            "moving averages, support and resistance, and confirmed swing labels."
        ),
        "unit": currency,
        "categories": categories,
        "candles": candles,
        "overlays": overlays,
        "levels": levels,
        "annotations": annotations,
    }
    spec_path = str(Path(out_path).with_suffix(".chart.json"))
    Path(spec_path).write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",    required=True)
    parser.add_argument("--mode",      choices=["research", "position", "setup"], default="research")
    parser.add_argument("--entry",     type=float)
    parser.add_argument("--stop",      type=float)
    parser.add_argument("--target",    type=float)
    parser.add_argument("--zone-low",  type=float)
    parser.add_argument("--zone-high", type=float)
    parser.add_argument("--days",      type=int, default=7)
    parser.add_argument("--interval",  choices=["1h", "3h", "1d", "1wk"], default="1h")
    parser.add_argument("--output",    type=str)
    args = parser.parse_args()

    symbol   = args.symbol.upper()
    if args.days < 2:
        parser.error("--days must be at least 2")
    for field in ("entry", "stop", "target", "zone_low", "zone_high"):
        value = getattr(args, field)
        if value is not None and value <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")
    if (args.zone_low is None) != (args.zone_high is None):
        parser.error("--zone-low and --zone-high must be provided together")
    if args.zone_low is not None and args.zone_low > args.zone_high:
        parser.error("--zone-low cannot exceed --zone-high")

    chart_dir = os.environ.get("STOCK_CHART_DIR") or os.path.join(tempfile.gettempdir(), "stock-market-charts")
    os.makedirs(chart_dir, exist_ok=True)
    out_path = args.output or os.path.join(chart_dir, f"chart_{symbol.replace('.', '_')}_{int(time.time())}.png")
    output_parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(output_parent, exist_ok=True)

    if args.interval == "1wk":
        df = fetch_weekly(symbol, trading_weeks=args.days)
    elif args.interval == "3h":
        df = fetch_3h(symbol, trading_days=args.days)
    elif args.interval == "1d":
        df = fetch_daily(symbol, trading_days=args.days)
    else:
        df = fetch_1h(symbol, trading_days=args.days)
    if df.empty or len(df) < 5:
        print(json.dumps({"error": f"Insufficient {args.interval} data for {symbol}"}))
        sys.exit(1)

    float_shares = fetch_float_shares(symbol)

    try:
        build_chart(
            symbol=symbol, df=df, mode=args.mode,
            entry=args.entry, stop=args.stop, target=args.target,
            zone_low=args.zone_low, zone_high=args.zone_high,
            out_path=out_path,
            float_shares=float_shares,
            trading_days=args.days,
            interval=args.interval,
        )
        chart_spec_path = write_chart_spec(
            symbol=symbol,
            df=df,
            mode=args.mode,
            entry=args.entry,
            stop=args.stop,
            target=args.target,
            zone_low=args.zone_low,
            zone_high=args.zone_high,
            out_path=out_path,
            interval=args.interval,
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    current = float(df["Close"].iloc[-1])
    print(
        json.dumps(
            {
                "path": out_path,
                "chart_spec_path": chart_spec_path,
                "symbol": symbol,
                "current_price": current,
            }
        )
    )


if __name__ == "__main__":
    main()
