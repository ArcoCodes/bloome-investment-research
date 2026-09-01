# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx", "beautifulsoup4", "pdfplumber"]
# ///
"""Fetch earnings call transcript from primary sources.

US stocks:  SEC EDGAR 8-K exhibits (Item 7.01 / Item 8.01) → plain text
HK stocks:  HKEX Disclosure search (Results Announcement + Presentation PDFs)
            + company IR site fallback

Usage:
    python scripts/fetch_earnings_transcript.py GOOGL          # US latest transcript
    python scripts/fetch_earnings_transcript.py 0700.HK        # HK latest transcript
    python scripts/fetch_earnings_transcript.py GOOGL --search # Search only, list candidates
    python scripts/fetch_earnings_transcript.py --url "https://..." # Direct URL

Output: JSON with symbol, market, filing_date, source_url,
        text_length, text_content, qa_section (Q&A extracted separately)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

EDGAR_UA = "NovarkBot research@novark.ai"
HKEX_UA  = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def is_hk_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".HK")


def _output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ============================================================
# US — SEC EDGAR 8-K transcript
# ============================================================

def _edgar_ticker_to_cik(ticker: str, client: httpx.Client) -> str | None:
    resp = client.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": EDGAR_UA, "Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        return None
    ticker_upper = ticker.upper()
    for entry in resp.json().values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)
    return None


def _edgar_get_recent_8k(cik: str, client: httpx.Client, max_items: int = 20) -> list[dict]:
    """Get recent 8-K filings via submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = client.get(url, headers={"User-Agent": EDGAR_UA}, timeout=15.0)
    if resp.status_code != 200:
        return []
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms     = recent.get("form", [])
    accessions= recent.get("accessionNumber", [])
    dates     = recent.get("filingDate", [])

    results = []
    for i, form in enumerate(forms):
        if form == "8-K" and len(results) < max_items:
            results.append({
                "accession":   accessions[i] if i < len(accessions) else "",
                "filing_date": dates[i] if i < len(dates) else "",
            })
    return results


def _edgar_get_filing_exhibits(cik: str, accession: str, client: httpx.Client) -> list[dict]:
    """Return list of exhibit files for an 8-K filing."""
    acc_nodash = accession.replace("-", "")
    cik_nodash = cik.lstrip("0")
    index_url  = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik_nodash}&type=8-K&dateb=&owner=include&count=40"
    )
    # Use the filing index JSON instead
    idx_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}"
        f"/{acc_nodash}/{accession}-index.json"
    )
    resp = client.get(idx_url, headers={"User-Agent": EDGAR_UA}, timeout=15.0)
    if resp.status_code != 200:
        return []
    items = resp.json().get("directory", {}).get("item", [])
    base  = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{acc_nodash}/"
    return [{"name": it.get("name",""), "url": base + it.get("name","")} for it in items]


def _is_transcript_exhibit(name: str) -> bool:
    """Heuristic: does this filename look like an earnings call transcript?"""
    name_lower = name.lower()
    transcript_keywords = ["transcript", "earnings-call", "earningscall", "call-script",
                           "conference-call", "ex99", "ex-99", "exhibit99", "exhibit-99"]
    return any(kw in name_lower for kw in transcript_keywords)


def _download_html_text(url: str, client: httpx.Client) -> str | None:
    resp = client.get(url, headers={"User-Agent": EDGAR_UA}, timeout=30.0)
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _download_pdf_text(url: str, client: httpx.Client) -> str | None:
    import io
    try:
        import pdfplumber
    except ImportError:
        return None
    resp = client.get(url, headers={"User-Agent": HKEX_UA}, timeout=60.0, follow_redirects=True)
    if resp.status_code != 200:
        return None
    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n\n".join(p for p in pages if p.strip())
        return re.sub(r"\n{3,}", "\n\n", text) if text.strip() else None
    except Exception:
        return None


def _extract_qa_section(text: str) -> str | None:
    """Extract Q&A portion from transcript text."""
    if not text:
        return None
    # Common markers for Q&A start
    qa_markers = [
        r"QUESTION[S\s]+AND ANSWER",
        r"Q\s*&\s*A\s+SESSION",
        r"Q\s*AND\s*A",
        r"QUESTIONS?\s+FROM\s+ANALYSTS?",
        r"ANALYST\s+Q&A",
        r"QUESTION[S]?\s*:",
        r"\u95ee\u7b54\u73af\u8282",
        r"\u5206\u6790\u5e08\u95ee\u7b54",
        r"\u63d0\u95ee\u73af\u8282",
    ]
    for marker in qa_markers:
        m = re.search(marker, text, re.IGNORECASE)
        if m:
            return text[m.start():].strip()
    return None


def _fetch_motley_fool_transcript(ticker: str, client: httpx.Client) -> dict | None:
    """Try to fetch transcript from Motley Fool (free, no login required for most)."""
    # Motley Fool transcript search URL
    search_url = f"https://www.fool.com/earnings/call-transcripts/?search={ticker}"
    try:
        resp = client.get(search_url, headers={"User-Agent": HKEX_UA}, timeout=15.0)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # Find first transcript link
        links = soup.find_all("a", href=True)
        transcript_links = [
            l["href"] for l in links
            if ticker.lower() in l.get("href","").lower()
            and "transcript" in l.get("href","").lower()
        ]
        if not transcript_links:
            return None
        url = transcript_links[0]
        if not url.startswith("http"):
            url = "https://www.fool.com" + url
        resp2 = client.get(url, headers={"User-Agent": HKEX_UA}, timeout=20.0)
        if resp2.status_code != 200:
            return None
        soup2 = BeautifulSoup(resp2.text, "html.parser")
        # Extract article content
        article = soup2.find("article") or soup2.find("div", class_=re.compile("article|content|transcript"))
        if not article:
            return None
        text = article.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) < 500:
            return None
        qa = _extract_qa_section(text)
        return {"source": "motley_fool", "url": url, "text": text, "qa": qa}
    except Exception:
        return None


def _fetch_press_release_as_fallback(cik: str, client: httpx.Client) -> dict | None:
    """Fallback: extract management commentary from earnings press release (EX-99.1 in 8-K)."""
    filings = _edgar_get_recent_8k(cik, client, max_items=10)
    for filing in filings:
        acc = filing["accession"]
        acc_nodash = acc.replace("-","")
        cik_nodash = cik.lstrip("0")
        idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{acc_nodash}/{acc}-index.htm"
        try:
            resp = client.get(idx_url, headers={"User-Agent": EDGAR_UA}, timeout=15.0)
            if resp.status_code != 200:
                continue
            # Find EX-99.1 (press release)
            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=True)
            ex991_links = [
                l["href"] for l in links
                if "ex99" in l["href"].lower() or "ex-99" in l["href"].lower()
                and l["href"].lower().endswith((".htm", ".html"))
            ]
            if not ex991_links:
                continue
            doc_url = ex991_links[0]
            if not doc_url.startswith("http"):
                doc_url = "https://www.sec.gov" + doc_url
            text = _download_html_text(doc_url, client)
            if not text or len(text) < 300:
                continue
            # Check it looks like earnings press release (not other 8-K types)
            has_earnings_keywords = any(k in text.lower() for k in [
                "revenue", "earnings per share", "net income", "operating income",
                "gross margin", "guidance", "fiscal"
            ])
            if not has_earnings_keywords:
                continue
            qa = _extract_qa_section(text)
            return {
                "source": "press_release_fallback",
                "filing_date": filing["filing_date"],
                "url": doc_url,
                "text": text,
                "qa": qa,
                "note": "This is the earnings press release, not a full call transcript. Q&A section may be absent."
            }
        except Exception:
            continue
    return None


def fetch_us_transcript(ticker: str, search_only: bool = False) -> dict:
    """Fetch US earnings call transcript from SEC EDGAR 8-K."""
    with httpx.Client(follow_redirects=True) as client:
        cik = _edgar_ticker_to_cik(ticker, client)
        if not cik:
            return {"error": f"CIK not found for {ticker}"}

        filings_8k = _edgar_get_recent_8k(cik, client, max_items=30)
        if not filings_8k:
            return {"error": "No 8-K filings found"}

        if search_only:
            return {"symbol": ticker, "market": "us", "candidates": filings_8k[:10]}

        # Iterate recent 8-Ks, look for transcript exhibit
        for filing in filings_8k:
            accession   = filing["accession"]
            filing_date = filing["filing_date"]
            exhibits    = _edgar_get_filing_exhibits(cik, accession, client)

            # First pass: look for explicit transcript files
            transcript_exhibits = [e for e in exhibits if _is_transcript_exhibit(e["name"])]

            # Second pass: look for EX-99.1 type exhibits (usually the press release / transcript)
            if not transcript_exhibits:
                transcript_exhibits = [
                    e for e in exhibits
                    if re.match(r"ex\d+", e["name"].lower()) and
                    e["name"].lower().endswith((".htm", ".html", ".txt"))
                ]

            for exhibit in transcript_exhibits:
                url  = exhibit["url"]
                name = exhibit["name"].lower()
                text = None

                if name.endswith((".htm", ".html")):
                    text = _download_html_text(url, client)
                elif name.endswith(".pdf"):
                    text = _download_pdf_text(url, client)
                elif name.endswith(".txt"):
                    resp = client.get(url, headers={"User-Agent": EDGAR_UA}, timeout=30.0)
                    text = resp.text if resp.status_code == 200 else None

                if not text or len(text) < 500:
                    continue

                # Verify it looks like a transcript (has speech patterns)
                is_transcript = any(kw in text.lower() for kw in [
                    "operator", "thank you", "question", "answer",
                    "ceo", "cfo", "good morning", "good afternoon",
                    "earnings call", "conference call",
                ])
                if not is_transcript:
                    continue

                qa = _extract_qa_section(text)
                return {
                    "symbol":       ticker.upper(),
                    "market":       "us",
                    "filing_date":  filing_date,
                    "source_url":   url,
                    "accession":    accession,
                    "text_length":  len(text),
                    "text_content": text,
                    "qa_section":   qa,
                    "qa_length":    len(qa) if qa else 0,
                }

        # Fallback 1: Motley Fool free transcript
        mf = _fetch_motley_fool_transcript(ticker, client)
        if mf:
            qa = mf.get("qa")
            return {
                "symbol":       ticker.upper(),
                "market":       "us",
                "source":       "motley_fool",
                "source_url":   mf["url"],
                "text_length":  len(mf["text"]),
                "text_content": mf["text"],
                "qa_section":   qa,
                "qa_length":    len(qa) if qa else 0,
            }

        # Fallback 2: Earnings press release (prepared remarks, no Q&A)
        pr = _fetch_press_release_as_fallback(cik, client)
        if pr:
            qa = pr.get("qa")
            return {
                "symbol":       ticker.upper(),
                "market":       "us",
                "filing_date":  pr["filing_date"],
                "source":       "press_release_fallback",
                "source_url":   pr["url"],
                "text_length":  len(pr["text"]),
                "text_content": pr["text"],
                "qa_section":   qa,
                "qa_length":    len(qa) if qa else 0,
                "note":         pr["note"],
            }

        return {
            "symbol": ticker.upper(),
            "market": "us",
            "error":  "No transcript found. Try --url with direct link from IR site or Seeking Alpha.",
            "checked_filings": len(filings_8k),
        }


# ============================================================
# HK — HKEX Disclosure Easy
# ============================================================

_HKEX_BASE = "https://www1.hkexnews.hk"


def _hkex_code(symbol: str) -> str:
    return symbol.upper().replace(".HK", "").zfill(5)


def _hkex_resolve_stock_id(code: str, client: httpx.Client) -> int | None:
    resp = client.get(
        f"{_HKEX_BASE}/search/prefix.do",
        params={"callback": "cb", "lang": "EN", "type": "A", "name": code, "market": "SEHK"},
        headers={"User-Agent": HKEX_UA}, timeout=10.0,
    )
    if resp.status_code != 200:
        return None
    text = resp.text.strip()
    try:
        data = json.loads(text[text.index("(")+1:text.rindex(")")])
    except Exception:
        return None
    for info in data.get("stockInfo", []):
        if info.get("code") == code:
            return info["stockId"]
    return None


def _hkex_search_presentations(code: str, client: httpx.Client) -> list[dict]:
    """Search HKEX for results announcements and presentation materials.
    Uses the same POST pattern as fetch_earnings_report.py."""
    page_url = f"{_HKEX_BASE}/search/titlesearch.xhtml?lang=EN"
    resp = client.get(page_url, headers={"User-Agent": HKEX_UA}, timeout=15.0)
    if resp.status_code != 200:
        return []
    html = resp.text
    vs_match  = re.search(r'javax\.faces\.ViewState.*?value="([^"]+)"', html)
    fid_match = re.search(r'form id="(j_idt\d+)"', html)
    sp_match  = re.search(r'action="([^"]+)"', html)
    if not (vs_match and fid_match and sp_match):
        return []
    view_state   = vs_match.group(1)
    form_id      = fid_match.group(1)
    session_path = sp_match.group(1)
    stock_id     = _hkex_resolve_stock_id(code, client)
    if not stock_id:
        return []

    today_str = datetime.now().strftime("%Y%m%d")
    results = []

    # t2Gcode=3 → Results; t2Gcode=9 → Other announcements (presentations live here)
    for t2Gcode in ["3", "9"]:
        post_data = {
            form_id: form_id,
            f"{form_id}:loadMoreRange": "100",
            "javax.faces.ViewState": view_state,
            "lang": "EN",
            "category": "0",
            "market": "SEHK",
            "searchType": "0",
            "documentType": "",
            "t1code": "10000",
            "t2Gcode": t2Gcode,
            "t2code": "-2",
            "stockId": str(stock_id),
            "from": "20230101",
            "to": today_str,
            "newsTitle": "",
        }
        try:
            resp2 = client.post(
                f"{_HKEX_BASE}{session_path}?lang=en",
                data=post_data,
                headers={
                    "User-Agent": HKEX_UA,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": page_url,
                    "Origin": _HKEX_BASE,
                },
                timeout=20.0,
            )
            soup = BeautifulSoup(resp2.text, "html.parser")
            for row in soup.find_all("tr"):
                links = row.find_all("a", href=True)
                pdf_links = [a for a in links if ".pdf" in a["href"].lower()]
                if not pdf_links:
                    continue
                for link in pdf_links:
                    href = link["href"]
                    title_text = link.get_text(strip=True)
                    if not title_text:
                        continue
                    full_url = href if href.startswith("http") else f"{_HKEX_BASE}{href}"
                    # Extract date
                    row_text = row.get_text()
                    date_matches = re.findall(r"(\d{2}/\d{2}/\d{4})", row_text)
                    date_text = ""
                    if date_matches:
                        parts = date_matches[0].split("/")
                        date_text = f"{parts[2]}/{parts[1]}/{parts[0]}"
                    # Filter for presentation/transcript relevance
                    title_lower = title_text.lower()
                    is_presentation = any(kw in title_lower for kw in [
                        "presentation", "transcript", "briefing", "webcast",
                        "analyst", "investor", "results announcement",
                        "\u696d\u7e3e", "\u7c21\u5831", "\u767c\u5e03\u6703", "annual results", "interim results",
                    ])
                    if is_presentation or t2Gcode == "9":
                        results.append({"title": title_text, "date": date_text, "url": full_url})
        except Exception:
            continue
    return results


def fetch_hk_transcript(symbol: str, search_only: bool = False) -> dict:
    """Fetch HK earnings presentation/transcript from HKEX."""
    hkex_code = _hkex_code(symbol)

    with httpx.Client(follow_redirects=True) as client:
        candidates = _hkex_search_presentations(hkex_code, client)

        if search_only or not candidates:
            return {
                "symbol":     symbol.upper(),
                "market":     "hk",
                "candidates": candidates,
                "note":       "HK transcripts not standardized; presentation PDFs listed above" if not candidates
                              else "Search results only",
            }

        # Try to download the most recent candidate
        for c in candidates[:3]:
            url = c.get("url", "")
            if not url:
                continue
            if not url.startswith("http"):
                url = "https://www1.hkexnews.hk" + url

            text = None
            if url.lower().endswith(".pdf"):
                text = _download_pdf_text(url, client)
            elif url.lower().endswith((".htm", ".html")):
                text = _download_html_text(url, client)

            if text and len(text) > 200:
                qa = _extract_qa_section(text)
                return {
                    "symbol":       symbol.upper(),
                    "market":       "hk",
                    "filing_date":  c.get("date", ""),
                    "title":        c.get("title", ""),
                    "source_url":   url,
                    "text_length":  len(text),
                    "text_content": text,
                    "qa_section":   qa,
                    "qa_length":    len(qa) if qa else 0,
                }

        return {
            "symbol":     symbol.upper(),
            "market":     "hk",
            "candidates": candidates,
            "error":      "Found candidates but could not download text",
        }


# ============================================================
# Direct URL download
# ============================================================

def fetch_from_url(url: str) -> dict:
    with httpx.Client(follow_redirects=True) as client:
        if url.lower().endswith(".pdf"):
            text = _download_pdf_text(url, client)
        else:
            text = _download_html_text(url, client)

        if not text:
            return {"error": "Failed to download or parse URL", "url": url}

        qa = _extract_qa_section(text)
        return {
            "source_url":   url,
            "text_length":  len(text),
            "text_content": text,
            "qa_section":   qa,
            "qa_length":    len(qa) if qa else 0,
        }


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch earnings call transcript")
    parser.add_argument("symbol",  nargs="?", help="Ticker (GOOGL) or HK code (0700.HK)")
    parser.add_argument("--url",   help="Direct URL to download")
    parser.add_argument("--search", action="store_true", help="Search only, list candidates")
    args = parser.parse_args()

    if args.url:
        _output(fetch_from_url(args.url))
        return

    if not args.symbol:
        print(json.dumps({"error": "Provide a ticker symbol or --url"}, ensure_ascii=False))
        sys.exit(1)

    symbol = args.symbol.strip()
    if is_hk_symbol(symbol):
        _output(fetch_hk_transcript(symbol, search_only=args.search))
    else:
        _output(fetch_us_transcript(symbol, search_only=args.search))


if __name__ == "__main__":
    main()
