# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx", "beautifulsoup4", "pdfplumber"]
# ///
"""Fetch earnings report full text from primary sources.

US stocks: SEC EDGAR (10-Q/10-K HTML → plain text)
HK stocks: HKEX Disclosure (search results page, PDF download with pdfplumber)

Usage:
    python scripts/fetch_earnings_report.py GOOGL                    # US latest quarterly
    python scripts/fetch_earnings_report.py GOOGL --type 10-K        # US annual
    python scripts/fetch_earnings_report.py 0700.HK                  # HK latest earnings
    python scripts/fetch_earnings_report.py 0700.HK --type annual    # HK annual
    python scripts/fetch_earnings_report.py 0700.HK --type interim   # HK interim
    python scripts/fetch_earnings_report.py 0700.HK --search         # HK search only (list filings)
    python scripts/fetch_earnings_report.py 0700.HK --category prospectus --search  # HK prospectus
    python scripts/fetch_earnings_report.py --url "https://..."      # Direct PDF URL download

Output: JSON with symbol, market, report_type, filing_date, source_url, text_length, text_content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

# ============================================================
# Common
# ============================================================

EDGAR_UA = "NovarkBot research@novark.ai"
HKEX_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def is_hk_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".HK")


def _output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ============================================================
# US Stocks — SEC EDGAR
# ============================================================


def _edgar_ticker_to_cik(ticker: str, client: httpx.Client) -> str | None:
    """Resolve ticker → 10-digit CIK string via SEC company_tickers.json."""
    resp = client.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": EDGAR_UA, "Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)
    return None


def _edgar_search_filings(
    cik: str, form_type: str, client: httpx.Client
) -> list[dict]:
    """Search EDGAR EFTS for recent filings of given form type."""
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": f'"CIK={cik}"',
        "forms": form_type,
        "dateRange": "custom",
        "startdt": "2020-01-01",
        "enddt": datetime.now().strftime("%Y-%m-%d"),
    }
    resp = client.get(
        url,
        params=params,
        headers={"User-Agent": EDGAR_UA, "Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("hits", {}).get("hits", [])


def _edgar_get_filing_index(cik: str, accession: str, client: httpx.Client) -> dict | None:
    """Fetch the filing index JSON for a given accession number."""
    acc_no_dash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_no_dash}/{accession}-index.json"
    resp = client.get(
        url,
        headers={"User-Agent": EDGAR_UA, "Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


def _edgar_find_primary_doc(index_data: dict, form_type: str) -> str | None:
    """From filing index, locate the primary HTML document URL."""
    if not index_data:
        return None
    directory = index_data.get("directory", {})
    items = directory.get("item", [])
    cik = str(index_data.get("cik", "")).lstrip("0")
    acc_raw = directory.get("name", "").split("/")[-1] if "/" in directory.get("name", "") else ""

    # Find primary document: prefer .htm files matching the form type
    htm_candidates = []
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith((".htm", ".html")):
            # Skip R files (XBRL viewer), filing index pages
            if name.startswith("R") and name[1:].split(".")[0].isdigit():
                continue
            if "index" in name.lower():
                continue
            htm_candidates.append(name)

    if not htm_candidates:
        return None

    # Prefer the largest .htm (usually the main document)
    # or the one matching a common pattern like {ticker}-{date}.htm
    primary = htm_candidates[0]
    for candidate in htm_candidates:
        # Heuristic: the primary filing doc is usually not a small exhibit
        if form_type.lower().replace("-", "") in candidate.lower().replace("-", ""):
            primary = candidate
            break

    # Build full URL
    parent_path = directory.get("name", "")
    return f"https://www.sec.gov/Archives/edgar/data/{parent_path}/{primary}"


def _edgar_download_html_text(url: str, client: httpx.Client) -> str | None:
    """Download HTML filing and extract plain text with BeautifulSoup."""
    resp = client.get(
        url,
        headers={"User-Agent": EDGAR_UA},
        timeout=30.0,
    )
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove script/style elements
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _edgar_submissions_api(cik: str, form_type: str, client: httpx.Client) -> list[dict]:
    """Use the EDGAR submissions API as an alternative to EFTS search."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = client.get(
        url,
        headers={"User-Agent": EDGAR_UA, "Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results = []
    for i, form in enumerate(forms):
        if form == form_type:
            results.append({
                "accession": accessions[i] if i < len(accessions) else "",
                "filing_date": dates[i] if i < len(dates) else "",
                "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
            })
    return results


def fetch_us_earnings_report(ticker: str, form_type: str = "10-Q") -> dict:
    """Fetch US earnings report from SEC EDGAR.

    Steps:
    1. ticker → CIK
    2. Search filings via submissions API
    3. Download primary HTML document
    4. Extract text with BeautifulSoup
    """
    result = {
        "symbol": ticker.upper(),
        "market": "us",
        "report_type": form_type,
        "filing_date": None,
        "source_url": None,
        "text_length": 0,
        "text_content": None,
    }

    with httpx.Client() as client:
        # Step 1: Resolve CIK
        cik = _edgar_ticker_to_cik(ticker, client)
        if not cik:
            result["error"] = f"Could not resolve CIK for ticker '{ticker}'"
            return result

        # Step 2: Find latest filing via submissions API
        filings = _edgar_submissions_api(cik, form_type, client)
        if not filings:
            result["error"] = f"No {form_type} filings found for {ticker} (CIK: {cik})"
            return result

        latest = filings[0]
        accession = latest["accession"]
        filing_date = latest["filing_date"]
        primary_doc = latest["primary_doc"]
        result["filing_date"] = filing_date

        # Step 3: Build primary document URL
        acc_no_dash = accession.replace("-", "")
        cik_num = cik.lstrip("0")
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_no_dash}/{primary_doc}"
        result["source_url"] = doc_url

        # Step 4: Download and extract text
        text = _edgar_download_html_text(doc_url, client)
        if text:
            result["text_content"] = text
            result["text_length"] = len(text)
        else:
            result["error"] = f"Failed to download/parse document from {doc_url}"

    return result


# ============================================================
# HK Stocks — HKEXnews
# ============================================================

# HK report type mapping
# t2code values from HKEX tier-two category JSON
HK_REPORT_TYPES = {
    "annual": {
        "title_keywords": ["annual results", "\u5168\u5e74\u696d\u7e3e", "\u5168\u5e74\u4e1a\u7ee9"],
        "label": "annual_results",
        "t2code": "13300",  # Final Results
    },
    "interim": {
        "title_keywords": ["interim results", "\u4e2d\u671f\u696d\u7e3e", "\u4e2d\u671f\u4e1a\u7ee9"],
        "label": "interim_results",
        "t2code": "13400",  # Interim Results
    },
    "quarterly": {
        "title_keywords": ["quarterly results", "\u5b63\u5ea6\u696d\u7e3e", "\u5b63\u5ea6\u4e1a\u7ee9"],
        "label": "quarterly_results",
        "t2code": "13600",  # Quarterly Results
    },
}

# HKEX tier-two codes for all earnings-related filings
_HKEX_RESULTS_T2CODES = ["13300", "13400", "13600"]

# HKEX document category codes for --category parameter
HK_CATEGORY_CODES = {
    "earnings": {
        "t1code": "10000",  # Announcements and Notices
        "t2Gcode": "3",     # Results group
        "t2code": "-2",     # All results sub-types (default)
        "label": "earnings",
    },
    "prospectus": {
        "t1code": "40000",  # Listing Documents
        "t2Gcode": "-2",    # All sub-types
        "t2code": "-2",
        "label": "prospectus",
    },
}

_HKEX_BASE = "https://www1.hkexnews.hk"


def _hkex_resolve_stock_id(code: str, client: httpx.Client) -> int | None:
    """Resolve a 5-digit HKEX stock code to the internal stockId via prefix.do JSONP API."""
    import json as _json

    resp = client.get(
        f"{_HKEX_BASE}/search/prefix.do",
        params={
            "callback": "cb",
            "lang": "EN",
            "type": "A",
            "name": code,
            "market": "SEHK",
        },
        headers={"User-Agent": HKEX_UA},
        timeout=10.0,
    )
    if resp.status_code != 200:
        return None

    text = resp.text.strip()
    # Parse JSONP: cb({...})
    start = text.index("(") + 1
    end = text.rindex(")")
    data = _json.loads(text[start:end])

    for info in data.get("stockInfo", []):
        if info.get("code") == code:
            return info["stockId"]
    return None


def _hkex_search_filings(
    stock_code: str,
    report_type: str | None = None,
    category: str = "earnings",
    client: httpx.Client | None = None,
) -> list[dict]:
    """Search HKEX disclosure for announcements.

    Uses a 2-step approach matching the HKEX JSF application:
    1. GET the titlesearch page to obtain a JSF session + ViewState token
    2. Resolve the stock code to an internal stockId via the prefix.do JSONP API
    3. POST the JSF form with proper session, ViewState, stockId and category filters

    Returns list of {title, url, date} dicts.
    """
    code = stock_code.upper().replace(".HK", "").zfill(5)
    own_client = client is None
    if own_client:
        client = httpx.Client()

    results = []
    try:
        # ---- Step 1: GET the page to obtain session cookie + ViewState ----
        page_url = f"{_HKEX_BASE}/search/titlesearch.xhtml?lang=EN"
        resp = client.get(page_url, headers={"User-Agent": HKEX_UA}, timeout=15.0)
        if resp.status_code != 200:
            return results

        html = resp.text

        vs_match = re.search(r'javax\.faces\.ViewState.*?value="([^"]+)"', html)
        fid_match = re.search(r'form id="(j_idt\d+)"', html)
        sp_match = re.search(r'action="([^"]+)"', html)
        if not (vs_match and fid_match and sp_match):
            return results

        view_state = vs_match.group(1)
        form_id = fid_match.group(1)
        session_path = sp_match.group(1)

        # ---- Step 2: Resolve stock code → internal stockId ----
        stock_id = _hkex_resolve_stock_id(code, client)
        if stock_id is None:
            results.append({"error": f"Could not resolve HKEX stockId for {code}"})
            return results

        # ---- Step 3: Determine category filter ----
        cat_config = HK_CATEGORY_CODES.get(category, HK_CATEGORY_CODES["earnings"])
        t1code = cat_config["t1code"]
        t2Gcode = cat_config["t2Gcode"]

        if category == "earnings" and report_type and report_type in HK_REPORT_TYPES:
            t2code = HK_REPORT_TYPES[report_type]["t2code"]
        else:
            t2code = cat_config["t2code"]

        today_str = datetime.now().strftime("%Y%m%d")

        # ---- Step 4: POST the JSF form ----
        post_data = {
            form_id: form_id,
            f"{form_id}:loadMoreRange": "100",
            "javax.faces.ViewState": view_state,
            "lang": "EN",
            "category": "0",
            "market": "SEHK",
            "searchType": "0",
            "documentType": "",
            "t1code": t1code,
            "t2Gcode": t2Gcode,
            "t2code": t2code,
            "stockId": str(stock_id),
            "from": "20100101",
            "to": today_str,
            "newsTitle": "",
        }

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

        if resp2.status_code != 200:
            return results

        result_html = resp2.text

        # ---- Step 5: Parse the result HTML for PDF links ----
        soup = BeautifulSoup(result_html, "html.parser")

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

                # Extract date (DD/MM/YYYY format used by HKEX)
                date_text = ""
                row_text = row.get_text()
                date_matches = re.findall(r"(\d{2}/\d{2}/\d{4})", row_text)
                if date_matches:
                    # Convert DD/MM/YYYY to YYYY/MM/DD for consistent sorting
                    raw = date_matches[0]
                    parts = raw.split("/")
                    date_text = f"{parts[2]}/{parts[1]}/{parts[0]}"

                results.append({
                    "title": title_text,
                    "url": full_url,
                    "date": date_text,
                })

        # Fallback: regex extraction if BeautifulSoup found nothing
        if not results:
            pdf_matches = re.findall(
                r'href="([^"]*\.pdf[^"]*)"[^>]*>([^<]+)',
                result_html,
                re.IGNORECASE,
            )
            for href, title_text in pdf_matches:
                title_clean = title_text.strip()
                if not title_clean:
                    continue
                full_url = href if href.startswith("http") else f"{_HKEX_BASE}{href}"
                results.append({
                    "title": title_clean,
                    "url": full_url,
                    "date": "",
                })

    except Exception as e:
        results.append({"error": str(e)})
    finally:
        if own_client:
            client.close()

    return results


def _hkex_filter_by_type(filings: list[dict], report_type: str | None) -> list[dict]:
    """Filter HKEX filings by report type keywords."""
    if not report_type or report_type not in HK_REPORT_TYPES:
        return filings

    keywords = HK_REPORT_TYPES[report_type]["title_keywords"]
    filtered = []
    for f in filings:
        title_lower = f.get("title", "").lower()
        if any(kw.lower() in title_lower for kw in keywords):
            filtered.append(f)
    return filtered if filtered else filings  # fallback to all if no match


def _download_pdf_text(url: str, client: httpx.Client) -> str | None:
    """Download PDF and extract text using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        return None

    import tempfile
    import os

    # Use a generic browser UA for broader compatibility
    ua = HKEX_UA if "hkexnews" in url else "Mozilla/5.0 (compatible; NovarkBot/1.0)"
    resp = client.get(url, headers={"User-Agent": ua}, timeout=60.0, follow_redirects=True)
    if resp.status_code != 200:
        return None

    # Write to temp file, then extract
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(resp.content)
        tmp_path = f.name

    try:
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts) if text_parts else None
    finally:
        os.unlink(tmp_path)


def _hkex_search_with_playwright(
    stock_code: str,
    report_type: str | None = None,
    category: str = "earnings",
) -> list[dict]:
    """Search HKEX disclosure using Playwright browser automation.

    This is the more reliable method — needed when httpx-based search
    returns no results (HKEX sometimes requires JS rendering).

    Requires: playwright, `PLAYWRIGHT_BROWSERS_PATH=/opt/browsers playwright install chromium`
    """
    import os
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/browsers")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [{"error": "playwright not installed — cannot use browser fallback"}]

    code = stock_code.upper().replace(".HK", "").zfill(5)
    search_title = ""
    if category == "earnings":
        search_title = "results"
        if report_type and report_type in HK_REPORT_TYPES:
            keywords = HK_REPORT_TYPES[report_type]["title_keywords"]
            for kw in keywords:
                if kw.isascii():
                    search_title = kw
                    break
    elif category == "prospectus":
        search_title = "prospectus"

    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = context.new_page()

            try:
                page.goto(
                    "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=EN",
                    wait_until="networkidle",
                    timeout=30000,
                )
                page.wait_for_selector("a.btn-blue", timeout=15000)

                # Close cookie banner if present
                try:
                    accept_btn = page.locator("button:has-text('Accept'), button:has-text('\u63a5\u53d7')")
                    if accept_btn.count() > 0 and accept_btn.first.is_visible():
                        accept_btn.first.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

                # Enter stock code
                stock_input = page.locator("#searchStockCode")
                if stock_input.count() > 0 and stock_input.is_visible():
                    stock_input.fill(code)
                    page.wait_for_timeout(1500)
                    # Select from autocomplete
                    rows = page.locator("table tr").all()
                    for row in rows:
                        try:
                            if code in row.inner_text():
                                row.click()
                                page.wait_for_timeout(500)
                                break
                        except Exception:
                            continue

                # Enter title keyword
                if search_title:
                    title_input = page.locator("#searchTitle")
                    if title_input.count() > 0 and title_input.is_visible():
                        title_input.fill(search_title)

                # Click search
                search_btn = page.locator("a.btn-blue")
                if search_btn.count() > 0:
                    search_btn.first.click()

                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(2000)

                # Extract PDF links — improved selector
                pdf_links = page.locator("a[href*='.pdf'], a[href*='listedco']").all()
                seen = set()
                for link in pdf_links[:50]:
                    try:
                        href = link.get_attribute("href")
                        if not href or ".pdf" not in href.lower():
                            continue
                        title_text = link.inner_text().strip()
                        if href and href not in seen:
                            seen.add(href)
                            full_url = href if href.startswith("http") else f"https://www1.hkexnews.hk{href}"
                            results.append({
                                "title": title_text,
                                "url": full_url,
                                "date": "",
                            })
                    except Exception:
                        continue

            except Exception as e:
                results.append({"error": f"Playwright search failed: {e}"})
            finally:
                browser.close()
    except Exception as e:
        results.append({"error": f"Playwright launch failed: {e}"})

    return results


def _pick_best_filing(filings: list[dict], category: str) -> dict:
    """From a list of filings, pick the most relevant one.

    For earnings: prefer titles containing "results" or the Chinese term for results over
    dividends, board meeting dates, etc.
    For prospectus: prefer titles containing 'prospectus'/'listing document'.
    Falls back to the first filing if no keyword match.
    """
    if not filings:
        return {}

    if category == "earnings":
        keywords = ["results", "\u696d\u7e3e", "\u4e1a\u7ee9", "annual report", "interim report"]
    elif category == "prospectus":
        keywords = ["prospectus", "listing document", "\u4e0a\u5e02\u6587\u4ef6", "\u62db\u80a1"]
    else:
        return filings[0]

    for f in filings:
        title = f.get("title", "").lower()
        if any(kw.lower() in title for kw in keywords):
            return f

    return filings[0]


def fetch_hk_earnings_report(
    symbol: str,
    report_type: str | None = None,
    search_only: bool = False,
    category: str = "earnings",
) -> dict:
    """Fetch HK earnings report or prospectus from HKEX disclosure.

    Steps:
    1. Search HKEX disclosure for announcements (httpx first, playwright fallback)
    2. Filter by report type (annual/interim/quarterly) — earnings only
    3. If search_only, return list of filings
    4. Otherwise, download PDF and extract text (pdfplumber) or return URL
    """
    if category == "earnings":
        label = HK_REPORT_TYPES.get(report_type, {}).get("label", "results") if report_type else "results"
    else:
        label = category

    result = {
        "symbol": symbol.upper(),
        "market": "hk",
        "report_type": label,
        "filing_date": None,
        "source_url": None,
        "text_length": 0,
        "text_content": None,
    }

    # Step 1: Search via HKEX JSF form (2-step: session + POST)
    with httpx.Client() as client:
        filings = _hkex_search_filings(symbol, report_type=report_type, category=category, client=client)

        # Fallback to playwright if httpx found nothing
        if not filings or (len(filings) == 1 and "error" in filings[0]):
            pw_filings = _hkex_search_with_playwright(symbol, report_type, category=category)
            if pw_filings:
                filings = pw_filings

        # Step 2: Filter by type (only for earnings category, not prospectus)
        if category == "earnings":
            filings = _hkex_filter_by_type(filings, report_type)

        if not filings:
            result["error"] = f"No earnings filings found for {symbol} on HKEX disclosure"
            return result

        # Step 3: Search-only mode
        if search_only:
            result["filings"] = filings[:20]
            result["filings_count"] = len(filings)
            return result

        # Step 4: Pick the best filing (prefer actual results over dividends/board meetings)
        latest = _pick_best_filing(filings, category)
        result["source_url"] = latest.get("url")
        result["filing_date"] = latest.get("date", "").replace("/", "-") or None

        pdf_url = latest.get("url", "")
        if pdf_url.lower().endswith(".pdf"):
            # Try pdfplumber extraction
            text = _download_pdf_text(pdf_url, client)
            if text:
                result["text_content"] = text
                result["text_length"] = len(text)
            else:
                # pdfplumber not available or extraction failed
                result["text_content"] = None
                result["note"] = (
                    "PDF found but text extraction requires pdfplumber. "
                    "Install with: pip install pdfplumber. "
                    "PDF URL available in source_url for manual download."
                )
        else:
            result["note"] = "Filing URL found but not a PDF. Check source_url."

    return result


# ============================================================
# Direct URL download mode
# ============================================================


def fetch_url_pdf(url: str) -> dict:
    """Download a PDF from a direct URL and extract text with pdfplumber.

    Used for company IR pages, quarterly reports, or any arbitrary PDF URL.
    """
    result = {
        "symbol": None,
        "market": None,
        "report_type": "url_download",
        "filing_date": None,
        "source_url": url,
        "text_length": 0,
        "text_content": None,
    }

    with httpx.Client() as client:
        text = _download_pdf_text(url, client)
        if text:
            result["text_content"] = text
            result["text_length"] = len(text)
        else:
            result["error"] = (
                "Failed to download or extract text from PDF. "
                "Ensure pdfplumber is installed and the URL is accessible."
            )

    return result


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="Fetch earnings report full text from primary sources (SEC EDGAR / HKEX)"
    )
    parser.add_argument("symbol", nargs="?", default=None, help="Stock symbol (e.g. GOOGL, 0700.HK)")
    parser.add_argument(
        "--type",
        dest="report_type",
        default=None,
        help="Report type. US: 10-Q (default), 10-K. HK: annual, interim, quarterly",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="HK only: search and list filings without downloading",
    )
    parser.add_argument(
        "--category",
        default="earnings",
        choices=["earnings", "prospectus"],
        help="HK only: document category (default: earnings)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Direct PDF URL to download and extract text from (skips search)",
    )
    args = parser.parse_args()

    # --url mode: direct PDF download
    if args.url:
        result = fetch_url_pdf(args.url)
        _output(result)
        return

    if not args.symbol:
        parser.error("symbol is required unless --url is provided")

    symbol = args.symbol.upper()

    if is_hk_symbol(symbol):
        # HK stock
        report_type = args.report_type  # annual / interim / quarterly / None
        result = fetch_hk_earnings_report(
            symbol, report_type, search_only=args.search, category=args.category,
        )
    else:
        # US stock
        form_type = args.report_type if args.report_type in ("10-Q", "10-K") else "10-Q"
        result = fetch_us_earnings_report(symbol, form_type)

    _output(result)


if __name__ == "__main__":
    main()
