#!/usr/bin/env python3
"""SEC EDGAR enrichment for the financial-analyst-agent pipeline.

Pulls official, audited financials and filings straight from the SEC's
public EDGAR REST API — no API key required (only a polite User-Agent).

What this adds on top of yfinance fundamentals:
- Revenue / Net Income / EPS (latest 10-K & 10-Q, actual filed numbers)
- Total Assets / Total Liabilities / Book Equity (balance sheet)
- Operating Cash Flow / Free Cash Flow proxy (cash flow statement)
- Filing dates for 10-K, 10-Q, 8-K (so Claude knows data freshness)
- Latest 8-K items (material events published since the last annual report)

The data is merged into each ticker's ``fundamentals`` dict so notebook 03
can drop it straight into ``enriched_portfolio.json`` for Claude.

Usage:
    from sec_edgar import enrich_fundamentals_with_edgar
    fundamentals[ticker] = enrich_fundamentals_with_edgar(ticker, fundamentals[ticker])
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import requests

# ── Config ──────────────────────────────────────────────────────────────────
# SEC requires a identifying User-Agent. Override via env if you like.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "hermes-financial-dashboard (local-agent@example.com)"
)

# SEC moved the XBRL / submissions endpoints to data.sec.gov.
# The ticker->CIK index still lives on www.sec.gov.
SEC_BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"

# Mapping of ticker -> CIK is cached locally so we don't hit it every run.
_TICKER_INDEX_URL = f"{SEC_BASE}/files/company_tickers.json"
_CIK_TO_NAME_URL = f"{SEC_BASE}/files/company_tickers.json"

# Tags we pull from the company facts frames.
# Each entry: json_key -> ordered list of us-gaap tags to try, in priority
# order. The first tag that has data wins (so we don't accidentally pick a
# stale historical tag just because it has a later end date).
FACT_TAGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["StockholdersEquity"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForPropertyPlantAndEquipment",
        "CapitalExpendituresIncurredButNotYetPaid",
    ],
}


class EdgarError(Exception):
    pass


# ── Cached ticker -> CIK index ────────────────────────────────────────────────
_TICKER_INDEX: dict | None = None


def _load_ticker_index(force: bool = False) -> dict:
    """Return {TICKER: {"cik": int, "title": str}}.

    Cached in memory for the process lifetime. The SEC file is ~10 MB but
    small enough to fetch once per pipeline run.
    """
    global _TICKER_INDEX
    if _TICKER_INDEX is not None and not force:
        return _TICKER_INDEX

    headers = {"User-Agent": SEC_USER_AGENT}
    resp = requests.get(_TICKER_INDEX_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    index = {}
    for entry in raw.values():
        ticker = (entry.get("ticker") or "").upper()
        if ticker:
            index[ticker] = {
                "cik": int(entry["cik_str"]),
                "title": entry.get("title", ""),
            }
    _TICKER_INDEX = index
    return index


def _resolve_cik(ticker: str) -> int | None:
    """Map a ticker to its CIK (10-digit zero-padded integer), or None."""
    ticker = ticker.upper().strip()
    index = _load_ticker_index()
    if ticker not in index:
        return None
    return index[ticker]["cik"]


# ── Frames (company facts) helpers ───────────────────────────────────────────
def _get_company_facts(cik: int) -> dict:
    """Fetch the full company facts (XBRL frames) JSON for a CIK."""
    url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json"
    headers = {"User-Agent": SEC_USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _pick_value(units: dict, picker: str):
    """From a facts[tag][units] structure, pick the best numeric value.

    picker:
      "annual"     -> latest FY annual (form 10-K) value
      "quarterly"  -> latest quarterly (form 10-Q) value
      "instant"    -> latest instant value regardless of form
    Returns (value, end_date_str) or (None, None).
    """
    if not units:
        return None, None
    # Prefer USD units, fall back to pure numbers.
    unit_key = "USD" if "USD" in units else next(iter(units))
    facts = units[unit_key]
    if not facts:
        return None, None

    candidates = []
    for f in facts:
        # Skip non-standard / non-fiscal-period facts.
        fy = f.get("fy")
        fp = f.get("fp")  # FY, Q1..Q3, CY
        form = f.get("form", "")
        end = f.get("end")
        val = f.get("val")
        if val is None or end is None:
            continue
        # Only keep actual filed numbers.
        if picker == "annual":
            # 10-K / 10-K/A always carry fp == "FY".
            if fp != "FY":
                continue
        elif picker == "quarterly":
            # 10-Q carries Q1/Q2/Q3; some filers tag the annual as FY on a
            # 10-Q form too — accept both.
            if form not in ("10-Q", "10-Q/A"):
                continue
            if fp not in ("Q1", "Q2", "Q3", "FY"):
                continue
        candidates.append((end, val, form))

    if not candidates:
        return None, None

    # Most recent by end date.
    candidates.sort(key=lambda x: x[0], reverse=True)
    end, val, _form = candidates[0]
    return val, end


def _extract_facts(company_facts: dict, picker: str) -> dict:
    """Walk the company facts and pull every metric we care about.

    For each json key we try all candidate us-gaap tags and keep the value
    from the tag whose *most recent* filing period is the newest. This
    matters because filers sometimes switch the primary revenue tag between
    fiscal years (e.g. AAPL used ``Revenues`` through FY2018, then switched
    to ``RevenueFromContractWithCustomerExcludingAssessedTax``) — picking the
    first tag with any data would lock us into a stale year.
    """
    out = {}
    facts = company_facts.get("facts", {}).get("us-gaap", {})
    for key, tags in FACT_TAGS.items():
        best = None  # (end, value)
        for tag in tags:
            if tag not in facts:
                continue
            val, end = _pick_value(facts[tag].get("units", {}), picker)
            if val is None or end is None:
                continue
            if best is None or end > best[0]:
                best = (end, val)
        if best is not None:
            out[key] = {"value": best[1], "period_end": best[0]}
    return out


# ── Submissions (filing dates + 8-K items) ────────────────────────────────────
def _get_submissions(cik: int) -> dict:
    url = f"{DATA_BASE}/submissions/CIK{cik:010d}.json"
    headers = {"User-Agent": SEC_USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _recent_filings(submissions: dict, forms: set, limit: int = 4) -> list[dict]:
    """Return recent filings of the given form types, newest first."""
    recent = submissions.get("filings", {}).get("recent", {})
    if not recent:
        return []
    out = []
    for i in range(len(recent.get("form", []))):
        form = recent["form"][i]
        if form not in forms:
            continue
        out.append(
            {
                "form": form,
                "filing_date": recent["filingDate"][i],
                "report_date": recent.get("reportDate", [None] * len(recent["form"]))[i],
                "primary_doc": recent.get("primaryDocument", [None] * len(recent["form"]))[i],
                "accession": recent.get("accessionNumber", [None] * len(recent["form"]))[i],
            }
        )
        if len(out) >= limit:
            break
    return out


def _latest_8k_items(submissions: dict) -> list[str]:
    """Pull the 4 most-recent 8-K filing dates so Claude knows what dropped."""
    filings = _recent_filings(submissions, {"8-K"}, limit=4)
    items = []
    for f in filings:
        # The items list isn't in the submissions API; we surface the date +
        # a link to the filing so Claude / the user can drill in.
        acc = (f["accession"] or "").replace("-", "")
        if acc:
            link = f"{SEC_BASE}/Archives/edgar/data/{f['cik'] if 'cik' in f else ''}"
            # link is informational; keep simple
        items.append(f"8-K filed {f['filing_date']}")
    return items


# ── Public API ─────────────────────────────────────────────────────────────────
def get_edgar_fundamentals(ticker: str) -> dict | None:
    """Return a dict of EDGAR-sourced fundamentals for ``ticker``.

    Returns None if the ticker isn't found in EDGAR (e.g. crypto, funds,
    or foreign listings without a US CIK).
    """
    cik = _resolve_cik(ticker)
    if cik is None:
        return None

    try:
        company_facts = _get_company_facts(cik)
    except requests.RequestException as e:
        raise EdgarError(f"EDGAR facts fetch failed for {ticker}: {e}")

    annual = _extract_facts(company_facts, "annual")
    quarterly = _extract_facts(company_facts, "quarterly")

    # Prefer annual for income-statement lines, quarterly for the most
    # recent snapshot of cash flow.
    result = {}
    income_keys = {"revenue", "net_income", "eps_diluted", "eps_basic"}
    for key, payload in annual.items():
        if key in income_keys:
            result[key] = payload
    for key, payload in quarterly.items():
        # Quarterly revenue/earnings are TTM-ish; keep both if annual missing.
        if key not in result or key in {"operating_cash_flow", "capex"}:
            result[key] = payload
    # Balance-sheet items: always take latest instant from annual facts.
    for key in {"total_assets", "total_liabilities", "stockholders_equity"}:
        if key in annual:
            result[key] = annual[key]

    # Filing dates + 8-K recency
    try:
        subs = _get_submissions(cik)
        result["_filings"] = {
            "latest_10k": _recent_filings(subs, {"10-K"}, 1),
            "latest_10q": _recent_filings(subs, {"10-Q"}, 1),
            "recent_8k": _latest_8k_items(subs),
        }
    except requests.RequestException:
        result["_filings"] = {}

    # Derived metrics
    ta = (annual.get("total_assets") or {}).get("value")
    tl = (annual.get("total_liabilities") or {}).get("value")
    eq = (annual.get("stockholders_equity") or {}).get("value")
    ni = (result.get("net_income") or {}).get("value")
    rev = (result.get("revenue") or {}).get("value")
    # Operating cash flow: prefer quarterly (most recent), fall back to annual.
    ocf = (result.get("operating_cash_flow") or {}).get("value")
    if ocf is None:
        ocf = (annual.get("operating_cash_flow") or {}).get("value")
    # Capex: try quarterly then annual so FCF works for any filer.
    capex = (quarterly.get("capex") or {}).get("value")
    if capex is None:
        capex = (annual.get("capex") or {}).get("value")

    if ta and tl:
        result["debt_to_assets"] = round(tl / ta, 3)
    if rev and ni is not None:
        result["net_margin_edgar"] = round(ni / rev, 4)
    if ocf is not None and capex is not None:
        result["free_cash_flow_edgar"] = ocf - capex
    if eq and ni is not None:
        result["roe_edgar"] = round(ni / eq, 4)

    # Flatten for storage: keep value + period_end + derived together.
    flat = {}
    for k, v in result.items():
        if k == "_filings":
            flat["filings"] = v
        elif isinstance(v, dict) and "value" in v:
            flat[k] = v["value"]
            flat[f"{k}_period_end"] = v.get("period_end")
        elif isinstance(v, (int, float)):
            # Derived metrics (debt_to_assets, net_margin, FCF, ROE) — keep as-is.
            flat[k] = v
    return flat


def enrich_fundamentals_with_edgar(ticker: str, fundamentals: dict) -> dict:
    """Merge EDGAR data into an existing yfinance ``fundamentals`` dict.

    Best-effort: any failure is swallowed and reported via a marker so the
    pipeline never breaks because the SEC hiccupped.
    """
    try:
        edgar = get_edgar_fundamentals(ticker)
    except EdgarError as e:
        fundamentals["edgar_error"] = str(e)
        return fundamentals

    if edgar is None:
        fundamentals["edgar"] = {"status": "not_found"}
        return fundamentals

    fundamentals["edgar"] = edgar
    # Promote a few EDGAR fields to the top level so Claude sees them without
    # digging into the nested dict, and so they line up with yfinance names.
    if edgar.get("revenue") and not fundamentals.get("revenue"):
        fundamentals["revenue"] = edgar["revenue"]
    if edgar.get("net_income") and not fundamentals.get("net_income"):
        fundamentals["net_income"] = edgar["net_income"]
    if edgar.get("total_assets") and not fundamentals.get("total_assets"):
        fundamentals["total_assets"] = edgar["total_assets"]
    if edgar.get("free_cash_flow_edgar") is not None:
        fundamentals["free_cash_flow_edgar"] = edgar["free_cash_flow_edgar"]
    if edgar.get("debt_to_assets") is not None:
        fundamentals["debt_to_assets"] = edgar["debt_to_assets"]
    if edgar.get("net_margin_edgar") is not None:
        fundamentals["net_margin_edgar"] = edgar["net_margin_edgar"]
    if edgar.get("roe_edgar") is not None:
        fundamentals["roe_edgar"] = edgar["roe_edgar"]
    return fundamentals


if __name__ == "__main__":
    # Smoke test: run with `uv run python sec_edgar.py AAPL NVDA TSLA`
    import sys

    test_tickers = sys.argv[1:] or ["AAPL", "NVDA", "TSLA"]
    for t in test_tickers:
        print(f"\n=== {t} ===")
        try:
            data = get_edgar_fundamentals(t)
            print(json.dumps(data, indent=2, default=str)[:1500])
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.2)  # be polite to SEC
