# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance", "akshare"]
# ///
"""Fetch macro environment data for a specific market.

Supports US and HK markets with market-specific indicators.

Usage:
    python scripts/fetch_macro.py           # defaults to US
    python scripts/fetch_macro.py --market us
    python scripts/fetch_macro.py --market hk

Output: JSON with macro indicators, risk level, and triggered flag.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests as _requests
from provider_runtime import require_provider_module

yf = require_provider_module("macro", "yfinance")

from cache_utils import load_cache, save_cache


# Market-specific symbols
MARKET_SYMBOLS = {
    "us": {
        "sp500_futures": "ES=F",
        "nasdaq_futures": "NQ=F",
        "sp500": "^GSPC",           # S&P 500 spot (used in closing mode)
        "nasdaq100": "^NDX",        # Nasdaq 100 spot (used in closing mode; corresponds to NQ=F)
        "dow": "^DJI",              # Dow Jones spot (used in closing mode; useful for divergence)
        "vix": "^VIX",
        "oil": "CL=F",
        "brent": "BZ=F",            # Brent crude
        "treasury_10y": "^TNX",
        "treasury_short": "^IRX",    # 13-week T-bill yield (short-term rate)
        "gold": "GC=F",
        "copper": "HG=F",
        "dxy": "DX-Y.NYB",          # US Dollar Index (ICE)
    },
    "hk": {
        "hsi": "^HSI",          # Hang Seng Index
        "hscei": "^HSCE",       # Hang Seng China Enterprises Index
        "vix": "^VIX",          # VIX still matters globally
        "usdcny": "CNY=X",      # USD/CNY
        "usdhkd": "HKD=X",      # USD/HKD
        "oil": "CL=F",          # Oil still matters globally
        "gold": "GC=F",         # Gold — global haven signal
        "dxy": "DX-Y.NYB",          # US Dollar Index (ICE, global impact)
    },
}

# Risk thresholds per market
THRESHOLDS = {
    "us": {
        "futures_drop_elevated": -1.5,
        "futures_drop_high": -2.0,
        "futures_drop_extreme": -3.0,
        "vix_elevated": 25,
        "vix_high": 30,
        "vix_extreme": 35,
        "vix_spike_elevated": 15,
        "vix_spike_high": 20,
        "vix_spike_extreme": 30,
        "oil_spike_elevated": 5,
        "oil_spike_high": 7,
        "oil_spike_extreme": 10,
        "gold_spike_elevated": 2,
        "gold_spike_high": 3,
        "gold_spike_extreme": 5,
        "copper_change_elevated": 2,
        "copper_change_high": 3,
        "copper_change_extreme": 5,
        "dxy_spike_elevated": 1.0,
        "dxy_spike_high": 1.5,
        "dxy_spike_extreme": 2.0,
    },
    "hk": {
        "index_drop_elevated": -1.5,
        "index_drop_high": -2.5,
        "index_drop_extreme": -4.0,
        "vix_elevated": 25,
        "vix_high": 30,
        "vix_extreme": 35,
        "cny_spike_elevated": 0.5,   # % daily change
        "cny_spike_high": 1.0,
        "cny_spike_extreme": 1.5,
        "oil_spike_elevated": 5,
        "oil_spike_high": 7,
        "oil_spike_extreme": 10,
        "gold_spike_elevated": 2,
        "gold_spike_high": 3,
        "gold_spike_extreme": 5,
        "dxy_spike_elevated": 1.0,
        "dxy_spike_high": 1.5,
        "dxy_spike_extreme": 2.0,
    },
}


def _fetch_one_symbol(name: str, symbol: str) -> tuple[str, dict | None, object]:
    """Fetch a single yfinance symbol. Returns (name, data_entry, timestamp)."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d")
        if hist is not None and len(hist) >= 1:
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
            change_pct = round(((price - prev) / prev) * 100, 2) if prev else 0
            return name, {"price": round(price, 2), "change_pct": change_pct}, hist.index[-1]
    except Exception:
        pass
    return name, None, None


def fetch_all(market: str) -> tuple[dict, str]:
    """Fetch all macro indicators for the given market (concurrent).

    Returns (data_dict, data_time_iso_string).
    """
    symbols = MARKET_SYMBOLS.get(market, MARKET_SYMBOLS["us"])
    data = {}
    latest_ts = None

    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futures = {pool.submit(_fetch_one_symbol, name, sym): name for name, sym in symbols.items()}
        for fut in as_completed(futures):
            name, entry, ts = fut.result()
            if entry is not None:
                data[name] = entry
                if latest_ts is None or (ts is not None and ts > latest_ts):
                    latest_ts = ts

    # Format data_time
    if latest_ts is not None:
        if hasattr(latest_ts, "tz") and latest_ts.tz is not None:
            data_time = latest_ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            data_time = str(latest_ts)
    else:
        data_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return data, data_time


def assess_risk_us(data: dict) -> tuple[str, list[str], bool]:
    """Assess US macro risk level."""
    t = THRESHOLDS["us"]
    risk_factors = []
    risk_score = 0

    # Futures
    sp_data = data.get("sp500_futures", {})
    nq_data = data.get("nasdaq_futures", {})
    sp_chg = sp_data.get("change_pct") if sp_data.get("price") is not None else None
    nq_chg = nq_data.get("change_pct") if nq_data.get("price") is not None else None

    if sp_chg is None and nq_chg is None:
        risk_factors.append("Futures data unavailable")
        risk_score += 1
    else:
        futures_values = [v for v in [sp_chg, nq_chg] if v is not None]
        min_futures = min(futures_values)

        if min_futures <= t["futures_drop_extreme"]:
            risk_factors.append(f"Futures crash {min_futures:.1f}%")
            risk_score += 3
        elif min_futures <= t["futures_drop_high"]:
            risk_factors.append(f"Futures drop {min_futures:.1f}%")
            risk_score += 2
        elif min_futures <= t["futures_drop_elevated"]:
            risk_factors.append(f"Futures decline {min_futures:.1f}%")
            risk_score += 1

    # VIX level
    vix = data.get("vix", {}).get("price")
    vix_chg = data.get("vix", {}).get("change_pct", 0)

    if vix is not None:
        if vix >= t["vix_extreme"]:
            risk_factors.append(f"VIX extreme ({vix:.1f})")
            risk_score += 3
        elif vix >= t["vix_high"]:
            risk_factors.append(f"VIX high ({vix:.1f})")
            risk_score += 2
        elif vix >= t["vix_elevated"]:
            risk_factors.append(f"VIX elevated ({vix:.1f})")
            risk_score += 1

    # VIX spike
    if vix_chg >= t["vix_spike_extreme"]:
        risk_factors.append(f"VIX spike extreme {vix_chg:.1f}%")
        risk_score += 3
    elif vix_chg >= t["vix_spike_high"]:
        risk_factors.append(f"VIX spike {vix_chg:.1f}%")
        risk_score += 2
    elif vix_chg >= t["vix_spike_elevated"]:
        risk_factors.append(f"VIX spike {vix_chg:.1f}%")
        risk_score += 1

    # Oil spike
    oil_chg = data.get("oil", {}).get("change_pct", 0)
    if abs(oil_chg) >= t["oil_spike_extreme"]:
        risk_factors.append(f"Oil spike extreme {oil_chg:+.1f}%")
        risk_score += 3
    elif abs(oil_chg) >= t["oil_spike_high"]:
        risk_factors.append(f"Oil spike {oil_chg:+.1f}%")
        risk_score += 2
    elif abs(oil_chg) >= t["oil_spike_elevated"]:
        risk_factors.append(f"Oil spike {oil_chg:+.1f}%")
        risk_score += 1

    # DXY (US Dollar Index) — strong dollar pressures equities & commodities
    dxy_chg = data.get("dxy", {}).get("change_pct", 0)
    if abs(dxy_chg) >= t["dxy_spike_extreme"]:
        risk_factors.append(f"DXY spike extreme {dxy_chg:+.1f}%")
        risk_score += 2
    elif abs(dxy_chg) >= t["dxy_spike_high"]:
        risk_factors.append(f"DXY spike {dxy_chg:+.1f}%")
        risk_score += 1
    elif abs(dxy_chg) >= t["dxy_spike_elevated"]:
        risk_factors.append(f"DXY move {dxy_chg:+.1f}%")
        risk_score += 1

    # Gold spike — haven demand signal
    gold_chg = data.get("gold", {}).get("change_pct", 0)
    if abs(gold_chg) >= t["gold_spike_extreme"]:
        risk_factors.append(f"Gold spike extreme {gold_chg:+.1f}%")
        risk_score += 2
    elif abs(gold_chg) >= t["gold_spike_high"]:
        risk_factors.append(f"Gold spike {gold_chg:+.1f}%")
        risk_score += 1
    elif abs(gold_chg) >= t["gold_spike_elevated"]:
        risk_factors.append(f"Gold move {gold_chg:+.1f}%")
        risk_score += 1

    # Copper — economic activity proxy
    copper_chg = data.get("copper", {}).get("change_pct", 0)
    if abs(copper_chg) >= t["copper_change_extreme"]:
        risk_factors.append(f"Copper extreme {copper_chg:+.1f}%")
        risk_score += 2
    elif abs(copper_chg) >= t["copper_change_high"]:
        risk_factors.append(f"Copper move {copper_chg:+.1f}%")
        risk_score += 1

    # Combined trigger
    if vix is not None and vix > 25 and min_futures <= -2.0:
        if risk_score < 5:
            risk_score = 5
        if "Combined: VIX + futures" not in str(risk_factors):
            risk_factors.append(f"Combined: VIX {vix:.0f} + futures {min_futures:.1f}%")

    level = _score_to_level(risk_score)
    triggered = (
        (vix is not None and vix >= 25)
        or min_futures <= -1.5
        or abs(oil_chg) >= 5
        or abs(gold_chg) >= 3
        or abs(dxy_chg) >= 1.0
    )
    return level, risk_factors, triggered


def _compute_term_spread(data: dict) -> float | None:
    """Compute 10Y - 3M term spread. Negative = inverted curve."""
    tnx = data.get("treasury_10y", {}).get("price")
    irx = data.get("treasury_short", {}).get("price")
    if tnx is not None and irx is not None:
        return round(tnx - irx, 2)
    return None


def identify_scenario(data: dict, market: str) -> dict | None:
    """Identify macro scenario from cross-asset signals.

    Returns dict with scenario name, signals list, and confidence (low/medium/high),
    or None if no clear scenario detected.
    """
    vix = data.get("vix", {}).get("price")
    vix_chg = data.get("vix", {}).get("change_pct", 0)
    oil_chg = data.get("oil", {}).get("change_pct", 0)
    gold_chg = data.get("gold", {}).get("change_pct", 0)
    dxy_chg = data.get("dxy", {}).get("change_pct", 0)

    if market == "us":
        copper_chg = data.get("copper", {}).get("change_pct", 0)
        brent_chg = data.get("brent", {}).get("change_pct", 0)
        term_spread = _compute_term_spread(data)
        sp_chg = data.get("sp500_futures", {}).get("change_pct", 0)
        nq_chg = data.get("nasdaq_futures", {}).get("change_pct", 0)
    else:
        copper_chg = 0
        brent_chg = 0
        term_spread = None
        sp_chg = 0
        nq_chg = 0

    scenarios = []

    # Geopolitical risk: gold up + oil up + VIX up + equities down
    geo_signals = []
    if gold_chg > 1.5:
        geo_signals.append(f"gold +{gold_chg:.1f}%")
    if oil_chg > 2:
        geo_signals.append(f"oil +{oil_chg:.1f}%")
    if vix_chg > 10:
        geo_signals.append(f"VIX +{vix_chg:.1f}%")
    if len(geo_signals) >= 2:
        confidence = "high" if len(geo_signals) >= 3 else "medium"
        scenarios.append({"name": "geopolitical_risk", "signals": geo_signals, "confidence": confidence})

    # Inflation expectations: yields up + gold up + copper up
    inf_signals = []
    tnx_chg = data.get("treasury_10y", {}).get("change_pct", 0)
    if tnx_chg > 2:
        inf_signals.append(f"10Y yield +{tnx_chg:.1f}%")
    if gold_chg > 1:
        inf_signals.append(f"gold +{gold_chg:.1f}%")
    if copper_chg > 1.5:
        inf_signals.append(f"copper +{copper_chg:.1f}%")
    if len(inf_signals) >= 2:
        confidence = "high" if len(inf_signals) >= 3 else "medium"
        scenarios.append({"name": "inflation_expectations", "signals": inf_signals, "confidence": confidence})

    # Recession fear: yield curve flat/inverted + copper down + VIX up
    rec_signals = []
    if term_spread is not None and term_spread < 0.2:
        label = "inverted" if term_spread < 0 else "flat"
        rec_signals.append(f"curve {label} ({term_spread:+.2f}%)")
    if copper_chg < -2:
        rec_signals.append(f"copper {copper_chg:.1f}%")
    if vix is not None and vix > 25:
        rec_signals.append(f"VIX {vix:.1f}")
    if len(rec_signals) >= 2:
        confidence = "high" if len(rec_signals) >= 3 else "medium"
        scenarios.append({"name": "recession_fear", "signals": rec_signals, "confidence": confidence})

    # Risk-on: VIX low/dropping + equities up + copper up
    risk_on_signals = []
    if vix is not None and vix < 18:
        risk_on_signals.append(f"VIX low ({vix:.1f})")
    elif vix_chg < -5:
        risk_on_signals.append(f"VIX dropping {vix_chg:.1f}%")
    if sp_chg > 0.5 or nq_chg > 0.5:
        risk_on_signals.append(f"futures up")
    if copper_chg > 1:
        risk_on_signals.append(f"copper +{copper_chg:.1f}%")
    if len(risk_on_signals) >= 2:
        confidence = "high" if len(risk_on_signals) >= 3 else "medium"
        scenarios.append({"name": "risk_on", "signals": risk_on_signals, "confidence": confidence})

    if not scenarios:
        return None

    # Return highest confidence scenario (or first if tied)
    scenarios.sort(key=lambda s: {"high": 3, "medium": 2, "low": 1}[s["confidence"]], reverse=True)
    return scenarios[0]


def assess_risk_hk(data: dict, fund_flows: dict | None = None) -> tuple[str, list[str], bool]:
    """Assess HK macro risk level."""
    if fund_flows is None:
        fund_flows = {}
    t = THRESHOLDS["hk"]
    risk_factors = []
    risk_score = 0

    # HSI / HSCEI
    hsi_chg = data.get("hsi", {}).get("change_pct", 0)
    hscei_chg = data.get("hscei", {}).get("change_pct", 0)
    min_index = min(hsi_chg, hscei_chg)

    if min_index <= t["index_drop_extreme"]:
        risk_factors.append(f"HK index crash {min_index:.1f}%")
        risk_score += 3
    elif min_index <= t["index_drop_high"]:
        risk_factors.append(f"HK index drop {min_index:.1f}%")
        risk_score += 2
    elif min_index <= t["index_drop_elevated"]:
        risk_factors.append(f"HK index decline {min_index:.1f}%")
        risk_score += 1

    # VIX (global sentiment)
    vix = data.get("vix", {}).get("price")
    if vix is not None:
        if vix >= t["vix_extreme"]:
            risk_factors.append(f"VIX extreme ({vix:.1f})")
            risk_score += 2
        elif vix >= t["vix_high"]:
            risk_factors.append(f"VIX high ({vix:.1f})")
            risk_score += 1
        elif vix >= t["vix_elevated"]:
            risk_factors.append(f"VIX elevated ({vix:.1f})")
            risk_score += 1

    # CNY move
    cny_chg = data.get("usdcny", {}).get("change_pct", 0)
    if abs(cny_chg) >= t["cny_spike_extreme"]:
        risk_factors.append(f"CNY spike extreme {cny_chg:+.1f}%")
        risk_score += 3
    elif abs(cny_chg) >= t["cny_spike_high"]:
        risk_factors.append(f"CNY spike {cny_chg:+.1f}%")
        risk_score += 2
    elif abs(cny_chg) >= t["cny_spike_elevated"]:
        risk_factors.append(f"CNY move {cny_chg:+.1f}%")
        risk_score += 1

    # Oil
    oil_chg = data.get("oil", {}).get("change_pct", 0)
    if abs(oil_chg) >= t["oil_spike_extreme"]:
        risk_factors.append(f"Oil spike extreme {oil_chg:+.1f}%")
        risk_score += 2
    elif abs(oil_chg) >= t["oil_spike_high"]:
        risk_factors.append(f"Oil spike {oil_chg:+.1f}%")
        risk_score += 1

    # DXY — strong dollar pressures HK/EM equities
    dxy_chg = data.get("dxy", {}).get("change_pct", 0)
    if abs(dxy_chg) >= t["dxy_spike_extreme"]:
        risk_factors.append(f"DXY spike extreme {dxy_chg:+.1f}%")
        risk_score += 2
    elif abs(dxy_chg) >= t["dxy_spike_high"]:
        risk_factors.append(f"DXY spike {dxy_chg:+.1f}%")
        risk_score += 1
    elif abs(dxy_chg) >= t["dxy_spike_elevated"]:
        risk_factors.append(f"DXY move {dxy_chg:+.1f}%")
        risk_score += 1

    # Gold — haven demand (relevant for HK as global signal)
    gold_chg = data.get("gold", {}).get("change_pct", 0)
    if abs(gold_chg) >= t["gold_spike_extreme"]:
        risk_factors.append(f"Gold spike extreme {gold_chg:+.1f}%")
        risk_score += 2
    elif abs(gold_chg) >= t["gold_spike_high"]:
        risk_factors.append(f"Gold spike {gold_chg:+.1f}%")
        risk_score += 1

    # Combined: HSI drop + CNY weakness
    if min_index <= -2.0 and cny_chg >= 0.5:
        if risk_score < 5:
            risk_score = 5
        risk_factors.append(f"Combined: HSI {min_index:.1f}% + CNY weakening {cny_chg:+.1f}%")

    # Southbound fund flow trend (daily level, not intraday)
    sb = fund_flows.get("southbound", {})
    sb_latest = sb.get("latest_daily", 0)
    sb_consec_out = sb.get("consecutive_outflow_days", 0)
    sb_avg = sb.get("avg_5d", 0)

    # Single day large outflow reversing prior inflow trend
    if sb_latest < -100 and sb_avg > 0:
        risk_factors.append(f"Southbound trend reversal: today {sb_latest:.0f} hundred million vs 5d avg {sb_avg:+.0f} hundred million")
        risk_score += 2
    # Sustained outflow (3+ consecutive days)
    elif sb_consec_out >= 3:
        risk_factors.append(f"Southbound sustained outflow: {sb_consec_out} consecutive days")
        risk_score += 1

    level = _score_to_level(risk_score)
    triggered = (
        min_index <= -1.5
        or abs(cny_chg) >= 0.5
        or (vix is not None and vix >= 25)
        or abs(oil_chg) >= 5
        or abs(gold_chg) >= 3
    )
    return level, risk_factors, triggered


def _score_to_level(score: int) -> str:
    if score >= 5:
        return "extreme"
    if score >= 3:
        return "high"
    if score >= 1:
        return "elevated"
    return "normal"


def _fetch_one_hsgt(code: str, label: str) -> tuple[str, dict | None]:
    """Fetch recent fund flow for one direction from eastmoney API.

    Fetches 10 rows (newest-first) then keeps up to 5 with non-null NET_DEAL_AMT.
    Northbound data stopped publishing after 2024-08, so it will return None.
    """
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "10",
            "pageNumber": "1",
            "reportName": "RPT_MUTUAL_DEAL_HISTORY",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(MUTUAL_TYPE="00{code}")',
        }
        r = _requests.get(url, params=params, timeout=10)
        rows = r.json().get("result", {}).get("data", [])
        if not rows:
            return label, None

        # Filter out null values, keep up to 5 most recent, reverse to chronological
        daily_net = [row["NET_DEAL_AMT"] for row in reversed(rows) if row.get("NET_DEAL_AMT") is not None][-5:]
        if not daily_net:
            return label, None

        avg_5d = sum(daily_net) / len(daily_net)
        consecutive_outflow = 0
        for v in reversed(daily_net):
            if v < 0:
                consecutive_outflow += 1
            else:
                break

        return label, {
            "latest_daily": round(daily_net[-1], 2),
            "avg_5d": round(avg_5d, 2),
            "consecutive_outflow_days": consecutive_outflow,
            "daily_values": [round(v, 2) for v in daily_net],
        }
    except Exception:
        return label, None


def fetch_hsgt_flows() -> dict:
    """Fetch 5-day daily northbound/southbound fund flows (concurrent).

    Uses eastmoney API directly with pageSize=5 instead of akshare full history.
    """
    result = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_fetch_one_hsgt, "5", "northbound"),
            pool.submit(_fetch_one_hsgt, "6", "southbound"),
        ]
        for fut in as_completed(futures):
            label, data = fut.result()
            if data is not None:
                result[label] = data
    return result


def main():
    parser = argparse.ArgumentParser(description="Fetch macro environment data")
    parser.add_argument("--market", default="us", choices=["us", "hk"],
                        help="Market to fetch macro data for (default: us)")
    parser.add_argument("--force", action="store_true", help="Skip cache")
    args = parser.parse_args()

    if not args.force:
        cached = load_cache("macro", args.market)
        if cached is not None:
            print(json.dumps(cached, indent=2))
            return

    if args.market == "hk":
        # Fetch yfinance data and fund flows concurrently
        with ThreadPoolExecutor(max_workers=2) as pool:
            yf_future = pool.submit(fetch_all, args.market)
            ff_future = pool.submit(fetch_hsgt_flows)
            data, data_time = yf_future.result()
            fund_flows = ff_future.result()
    else:
        data, data_time = fetch_all(args.market)

    if args.market == "hk":
        level, factors, triggered = assess_risk_hk(data, fund_flows)
        hsi_data = data.get("hsi", {})
        hscei_data = data.get("hscei", {})
        cny_data = data.get("usdcny", {})
        hkd_data = data.get("usdhkd", {})
        vix_data = data.get("vix", {})
        oil_data = data.get("oil", {})
        gold_data = data.get("gold", {})
        dxy_data = data.get("dxy", {})

        scenario = identify_scenario(data, "hk")

        result = {
            "market": "hk",
            "data_time": data_time,
            "indices": {
                "hsi": {"price": hsi_data.get("price"), "change_pct": hsi_data.get("change_pct")},
                "hscei": {"price": hscei_data.get("price"), "change_pct": hscei_data.get("change_pct")},
            },
            "fx": {
                "usdcny": {"price": cny_data.get("price"), "change_pct": cny_data.get("change_pct")},
                "usdhkd": {"price": hkd_data.get("price"), "change_pct": hkd_data.get("change_pct")},
                "dxy": {"price": dxy_data.get("price"), "change_pct": dxy_data.get("change_pct")},
            },
            "global": {
                "vix": {"price": vix_data.get("price"), "change_pct": vix_data.get("change_pct")},
                "oil": {"price": oil_data.get("price"), "change_pct": oil_data.get("change_pct")},
                "gold": {"price": gold_data.get("price"), "change_pct": gold_data.get("change_pct")},
            },
            "fund_flows": {
                "northbound": fund_flows.get("northbound"),
                "southbound": fund_flows.get("southbound"),
            },
            "scenario": scenario,
            "risk_level": level,
            "risk_factors": factors,
            "triggered": triggered,
        }
    else:
        level, factors, triggered = assess_risk_us(data)
        vix_data = data.get("vix", {})
        sp_data = data.get("sp500_futures", {})
        nq_data = data.get("nasdaq_futures", {})
        oil_data = data.get("oil", {})
        brent_data = data.get("brent", {})
        tnx_data = data.get("treasury_10y", {})
        irx_data = data.get("treasury_short", {})
        dxy_data = data.get("dxy", {})
        gold_data = data.get("gold", {})
        copper_data = data.get("copper", {})

        term_spread = _compute_term_spread(data)
        scenario = identify_scenario(data, "us")

        result = {
            "market": "us",
            "data_time": data_time,
            "indices": {
                "sp500_futures": {"price": sp_data.get("price"), "change_pct": sp_data.get("change_pct")},
                "nasdaq_futures": {"price": nq_data.get("price"), "change_pct": nq_data.get("change_pct")},
            },
            "volatility": {
                "vix": {"price": vix_data.get("price"), "change_pct": vix_data.get("change_pct")},
            },
            "fx": {
                "dxy": {"price": dxy_data.get("price"), "change_pct": dxy_data.get("change_pct")},
            },
            "commodities": {
                "oil": {"price": oil_data.get("price"), "change_pct": oil_data.get("change_pct")},
                "brent": {"price": brent_data.get("price"), "change_pct": brent_data.get("change_pct")},
                "gold": {"price": gold_data.get("price"), "change_pct": gold_data.get("change_pct")},
                "copper": {"price": copper_data.get("price"), "change_pct": copper_data.get("change_pct")},
            },
            "rates": {
                "treasury_10y": {"price": tnx_data.get("price"), "change_pct": tnx_data.get("change_pct")},
                "treasury_short": {"price": irx_data.get("price"), "change_pct": irx_data.get("change_pct")},
                "term_spread": term_spread,
            },
            "scenario": scenario,
            "risk_level": level,
            "risk_factors": factors,
            "triggered": triggered,
        }

    print(json.dumps(result, indent=2))
    save_cache("macro", args.market, result)


if __name__ == "__main__":
    main()
