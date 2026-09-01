#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
fetch_13f.py — Retrieve a specified hedge fund's two most recent 13F holdings and calculate quarterly changes

Usage:
  python scripts/fetch_13f.py --cik 0001423298              # Use CIK
  python scripts/fetch_13f.py --name citadel                # Use short_name (look up fund-cik.json)
  python scripts/fetch_13f.py --cik 0001423298 -o /tmp/out.json

Output: JSON → stdout (also writes $SKILL_CACHE_DIR/13f-{short_name}.json)

Data source: free SEC EDGAR API (no key required; limit 10 requests/second)
  - submissions: https://data.sec.gov/submissions/CIK{cik}.json
  - filing index: https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/
  - holdings XML: locate the InfoTable file from the index
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree
from typing import Optional

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR  = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
CACHE_DIR  = os.environ.get("SKILL_CACHE_DIR", os.path.join(__import__("tempfile").gettempdir(), "stock-market-data-cache", "13f"))

EDGAR_BASE = "https://data.sec.gov"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
HEADERS = {
    "User-Agent": "Novark Research contact@novark.com",   # SEC requires contact information
    "Accept": "application/json",
}


# ══════════════════════════════════════════════════════════════
# EDGAR API helpers
# ══════════════════════════════════════════════════════════════

def _get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": accept})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _get_json(url: str) -> dict:
    return json.loads(_get(url))


def _get_xml(url: str) -> ElementTree.Element:
    raw = _get(url, accept="application/xml")
    # Remove the namespace to simplify XPath
    raw_str = re.sub(r' xmlns[^"]*"[^"]*"', "", raw.decode("utf-8", errors="replace"))
    return ElementTree.fromstring(raw_str)


def normalize_cik(cik: str) -> str:
    """Ensure the CIK is zero-padded to 10 digits."""
    return cik.lstrip("0").zfill(10)


# ══════════════════════════════════════════════════════════════
# Look up fund CIK
# ══════════════════════════════════════════════════════════════

def cik_from_name(short_name: str) -> Optional[str]:
    """Look up a CIK from fund-cik.json."""
    cik_file = os.path.join(SKILL_DIR, "references", "fund-cik.json")
    if not os.path.exists(cik_file):
        return None
    data = json.loads(open(cik_file).read())
    for fund in data.get("funds", []):
        if fund["short_name"].lower() == short_name.lower():
            return fund["cik"]
    return None


def fund_meta(cik: str) -> dict:
    """Find fund metadata in fund-cik.json."""
    cik_file = os.path.join(SKILL_DIR, "references", "fund-cik.json")
    if os.path.exists(cik_file):
        data = json.loads(open(cik_file).read())
        for fund in data.get("funds", []):
            if normalize_cik(fund["cik"]) == normalize_cik(cik):
                return fund
    return {"name": cik, "cik": cik, "short_name": cik}


# ══════════════════════════════════════════════════════════════
# Retrieve the list of 13F filings
# ══════════════════════════════════════════════════════════════

def get_13f_filings(cik: str, limit: int = 4) -> list[dict]:
    """
    Retrieve the most recent N 13F-HR filings from the EDGAR submissions API.
    Return [{"accession": "...", "date": "...", "quarter": "..."}, ...]
    """
    cik_padded = normalize_cik(cik)
    url = f"{EDGAR_BASE}/submissions/CIK{cik_padded}.json"
    print(f"  Retrieving filing list: {url}", file=sys.stderr)
    data = _get_json(url)

    recent = data.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    acc_nos = recent.get("accessionNumber", [])
    dates   = recent.get("filingDate", [])

    results = []
    for form, acc, date in zip(forms, acc_nos, dates):
        if form in ("13F-HR", "13F-HR/A") and len(results) < limit:
            # Infer the quarter from the filing date
            d = datetime.strptime(date, "%Y-%m-%d")
            # A 13F filing reports data for the prior quarter
            # A filing within 45 days after Q1 end contains Q1 data
            quarter_month = (d.month - 1) // 3 * 3 + 1  # First month of the filing quarter
            # Simple back-calculation from filing month: filed within 45 days after quarter end
            # Use period_of_report directly
            results.append({
                "accession": acc,
                "date":      date,
            })
    return results


def get_period_of_report(cik: str, acc_no: str) -> str:
    """Get periodOfReport (the actual data cutoff) from the filing index."""
    cik_num = normalize_cik(cik).lstrip("0")
    acc_clean = acc_no.replace("-", "")
    url = f"{EDGAR_ARCHIVES}/{cik_num}/{acc_clean}/{acc_no}-index.json"
    try:
        data = _get_json(url)
        return data.get("periodOfReport", "")
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════
# Parse 13F holdings XML
# ══════════════════════════════════════════════════════════════

def get_holdings_xml_url(cik: str, acc_no: str) -> Optional[str]:
    """
    Find the InfoTable XML file URL from the filing index.
    It is usually named primary_doc.xml or infotable.xml.
    """
    cik_num = normalize_cik(cik).lstrip("0")
    acc_clean = acc_no.replace("-", "")
    index_url = f"{EDGAR_ARCHIVES}/{cik_num}/{acc_clean}/{acc_no}-index.json"
    try:
        index = _get_json(index_url)
        for doc in index.get("documents", []):
            name = doc.get("documentName", "").lower()
            desc = doc.get("description", "").lower()
            if ("infotable" in name or "primary_doc" in name or
                    "information table" in desc or "13f" in desc):
                return f"{EDGAR_ARCHIVES}/{cik_num}/{acc_clean}/{doc['documentName']}"
        # Fallback: use the first .xml file
        for doc in index.get("documents", []):
            if doc.get("documentName", "").lower().endswith(".xml"):
                return f"{EDGAR_ARCHIVES}/{cik_num}/{acc_clean}/{doc['documentName']}"
    except Exception as e:
        print(f"  ⚠ Index parse failed {acc_no}: {e}", file=sys.stderr)
    return None


def parse_holdings(xml_url: str) -> list[dict]:
    """
    Parse 13F InfoTable XML and return the holdings list.
    Each item: {name, cusip, value_k, shares, put_call, pct}
    value_k: holding market value in thousands of US dollars, the official unit
    """
    print(f"  Parsing holdings: {xml_url}", file=sys.stderr)
    try:
        root = _get_xml(xml_url)
    except Exception as e:
        print(f"  ⚠ XML parse failed: {e}", file=sys.stderr)
        return []

    holdings = []
    # Support both namespace formats
    for entry in root.iter("infoTable"):
        name    = (entry.findtext("nameOfIssuer") or "").strip()
        cusip   = (entry.findtext("cusip") or "").strip()
        val_str = entry.findtext("value") or "0"
        try:
            value_k = int(float(val_str.replace(",", "")))
        except ValueError:
            value_k = 0

        shr_el  = entry.find("shrsOrPrnAmt")
        shares  = 0
        if shr_el is not None:
            shr_str = shr_el.findtext("sshPrnamt") or "0"
            try:
                shares = int(float(shr_str.replace(",", "")))
            except ValueError:
                shares = 0

        put_call = (entry.findtext("putCall") or "").strip()

        if name and value_k > 0:
            holdings.append({
                "name":     name,
                "cusip":    cusip,
                "value_k":  value_k,   # Thousands of US dollars
                "shares":   shares,
                "put_call": put_call,   # "Put" | "Call" | ""
            })

    # Calculate weights
    total = sum(h["value_k"] for h in holdings)
    for h in holdings:
        h["pct"] = round(h["value_k"] / total * 100, 2) if total > 0 else 0

    # Sort by market value descending
    holdings.sort(key=lambda x: x["value_k"], reverse=True)
    return holdings


# ══════════════════════════════════════════════════════════════
# Quarterly comparison
# ══════════════════════════════════════════════════════════════

def compare_quarters(curr: list[dict], prev: list[dict]) -> dict:
    """
    Compare two periods and return categorized changes.
    Use cusip as the primary key, falling back to name when cusip is empty.
    """
    def key(h): return h["cusip"] if h["cusip"] else h["name"]

    curr_map = {key(h): h for h in curr}
    prev_map = {key(h): h for h in prev}

    new_positions, exits, increased, decreased = [], [], [], []

    for k, h in curr_map.items():
        if k not in prev_map:
            new_positions.append(h)
        else:
            p = prev_map[k]
            if p["value_k"] == 0:
                continue
            chg_pct = (h["value_k"] - p["value_k"]) / p["value_k"] * 100
            h["chg_pct"]    = round(chg_pct, 1)
            h["prev_value_k"] = p["value_k"]
            h["prev_shares"]  = p["shares"]
            if chg_pct >= 20:
                increased.append(h)
            elif chg_pct <= -20:
                decreased.append(h)

    for k, h in prev_map.items():
        if k not in curr_map:
            exits.append(h)

    # Sort by largest magnitude first
    increased.sort(key=lambda x: x.get("chg_pct", 0), reverse=True)
    decreased.sort(key=lambda x: x.get("chg_pct", 0))
    new_positions.sort(key=lambda x: x["value_k"], reverse=True)
    exits.sort(key=lambda x: x["value_k"], reverse=True)

    return {
        "new_positions": new_positions[:20],
        "exits":         exits[:20],
        "increased":     increased[:20],
        "decreased":     decreased[:20],
    }


# ══════════════════════════════════════════════════════════════
# Main function
# ══════════════════════════════════════════════════════════════

def fetch_fund(cik: str) -> dict:
    meta = fund_meta(cik)
    print(f"📦 Retrieving {meta['name']} ({cik}) 13F holdings...", file=sys.stderr)

    # Retrieve the two most recent filings
    filings = get_13f_filings(cik, limit=2)
    if not filings:
        print("  ⚠ No 13F filing records found", file=sys.stderr)
        return {"error": "no_filings", "cik": cik}

    time.sleep(0.5)
    results = []
    for filing in filings:
        acc = filing["accession"]
        print(f"  Filing {acc} ({filing['date']}) ...", file=sys.stderr)

        # Add the reporting period
        time.sleep(0.2)
        period = get_period_of_report(cik, acc)

        # Find the holdings XML URL
        time.sleep(0.2)
        xml_url = get_holdings_xml_url(cik, acc)
        if not xml_url:
            print("  ⚠ Holdings XML not found; skipping", file=sys.stderr)
            continue

        time.sleep(0.3)
        holdings = parse_holdings(xml_url)
        total_value_m = sum(h["value_k"] for h in holdings) // 1000  # Millions of US dollars

        results.append({
            "accession":   acc,
            "filed_date":  filing["date"],
            "period":      period,
            "total_value_m": total_value_m,
            "position_count": len(holdings),
            "holdings":    holdings,
        })

    if not results:
        return {"error": "parse_failed", "cik": cik}

    output = {
        "fetched_at": datetime.now(CST).isoformat(),
        "fund": {
            "name":       meta.get("name", cik),
            "cik":        cik,
            "short_name": meta.get("short_name", cik),
        },
        "latest":   results[0],
        "previous": results[1] if len(results) > 1 else None,
        "changes":  compare_quarters(
            results[0]["holdings"],
            results[1]["holdings"] if len(results) > 1 else []
        ) if len(results) > 1 else None,
    }

    # Compact output: retain the top 50 holdings because the full list is too large
    output["latest"]["top_holdings"]  = output["latest"]["holdings"][:50]
    if output["previous"]:
        output["previous"]["top_holdings"] = output["previous"]["holdings"][:50]
    # Remove the full list to save space
    del output["latest"]["holdings"]
    if output["previous"]:
        del output["previous"]["holdings"]

    return output


def main():
    parser = argparse.ArgumentParser(description="Retrieve hedge-fund 13F holdings")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cik",  help="Fund CIK, for example 0001423298")
    group.add_argument("--name", help="Fund short_name, for example citadel")
    parser.add_argument("-o", "--output", help="Output JSON path (defaults to SKILL_CACHE_DIR)")
    args = parser.parse_args()

    cik = args.cik
    if args.name:
        cik = cik_from_name(args.name)
        if not cik:
            print(f"⚠ {args.name} not found; check fund-cik.json", file=sys.stderr)
            sys.exit(1)

    data = fetch_fund(cik)

    os.makedirs(CACHE_DIR, exist_ok=True)
    short_name = data.get("fund", {}).get("short_name", cik)
    out_path = args.output or os.path.join(CACHE_DIR, f"13f-{short_name}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ {short_name} → {out_path}", file=sys.stderr)
    print(json.dumps({
        "status":        "ok",
        "fund":          data.get("fund", {}).get("name"),
        "latest_period": data.get("latest", {}).get("period"),
        "positions":     data.get("latest", {}).get("position_count"),
        "total_value_m": data.get("latest", {}).get("total_value_m"),
        "path":          out_path,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
