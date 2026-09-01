"""Shared utility: resample hourly OHLCV to session-based 4H candles.

TradingView-style: split each trading day into AM/PM sessions,
producing exactly 2 candles per day. Pre-market and after-hours bars
are filtered out.

US stocks (ET): bar0 = 9:30-13:30, bar1 = 13:30-16:00
HK stocks (HKT): bar0 = 9:30-12:00, bar1 = 13:00-16:00
"""

from __future__ import annotations

import pandas as pd


def _is_hk_symbol(symbol: str) -> bool:
    s = symbol.upper()
    return s.endswith(".HK") or s.endswith(".SS") or s.endswith(".SZ")


def resample_hourly_to_4h(hourly_df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """Resample 1H OHLCV to session-based 4H candles.

    Assumes hourly_df index is tz-naive local time (ET for US, HKT for HK).
    Returns a DataFrame with DatetimeIndex (first bar's timestamp per window).
    """
    if hourly_df.empty:
        return pd.DataFrame()

    df = hourly_df.copy()
    hours = df.index.hour

    # Filter to regular-session hours only
    if _is_hk_symbol(symbol):
        # HK: 9-11 (morning) and 13-15 (afternoon), lunch break 12:00-13:00
        mask = ((hours >= 9) & (hours <= 11)) | ((hours >= 13) & (hours <= 15))
    else:
        # US: 9-15 (9:30 AM - 4:00 PM ET, hour 9 through 15)
        mask = (hours >= 9) & (hours <= 15)

    df = df.loc[mask]
    if df.empty:
        return pd.DataFrame()

    # Assign session window: 0 = first half (hour < 13), 1 = second half (hour >= 13)
    df["_date"] = df.index.date
    df["_window"] = (df.index.hour >= 13).astype(int)

    rows = []
    timestamps = []
    for (_date, _win), chunk in df.groupby(["_date", "_window"]):
        if chunk.empty:
            continue
        rows.append({
            "Open": chunk["Open"].iloc[0],
            "High": chunk["High"].max(),
            "Low": chunk["Low"].min(),
            "Close": chunk["Close"].iloc[-1],
            "Volume": chunk["Volume"].sum(),
        })
        timestamps.append(chunk.index[0])

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
    result.index.name = "Datetime"
    result.attrs.update(df.attrs)
    result.attrs["transformation"] = "regular-session hourly bars resampled into two session-half bars per day"
    return result.sort_index()
