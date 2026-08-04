#!/usr/bin/env python3
"""FRED macro enrichment for the financial-analyst-agent pipeline.

Pulls U.S. macro indicators from the Federal Reserve Economic Data (FRED)
API and attaches them to the enriched portfolio so Claude can read the

economic backdrop (inflation, policy rate, yield curve, unemployment) when
it writes each recommendation.

FREE: 120,000 calls/month, no rate limit on the key itself. Get a key at
https://fredaccount.stlouisfed.org/apisignup (free, instant).

Series pulled (with what they tell Claude):
  FEDFUNDS  Federal Funds Rate        -> policy tightness
  CPIAUCSL  CPI (all items, NSA)      -> headline inflation (YoY computed)
  PCEPI     PCE Price Index           -> Fed's preferred inflation gauge (YoY)
  UNRATE    Unemployment Rate         -> labor-market heat
  DGS10     10-Year Treasury Yield    -> long-rate / discount rate
  DGS2      2-Year Treasury Yield     -> short-rate (=> 10Y-2Y spread = curve)
  GDP       Nominal GDP               -> aggregate demand level
  INTRATIO  -> (computed) yield-curve spread 10Y-2Y (inversion = recession risk)

The module degrades gracefully: with no key it returns a marker dict and the
pipeline keeps running. Best-effort on any individual series failure.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

import requests

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Series id -> human label
SERIES = {
    "FEDFUNDS": "Federal Funds Rate",
    "CPIAUCSL": "CPI (inflation, NSA)",
    "UNRATE": "Unemployment Rate",
    "DGS10": "10Y Treasury Yield",
    "DGS2": "2Y Treasury Yield",
    "GDP": "Nominal GDP",
    "PCEPI": "PCE Price Index (core inflation proxy)",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "fin-dashboard/0.2 (personal use)"})

_last_call = 0.0


def _throttle():
    """Cap to ~10 req/sec to respect FRED guidance."""
    global _last_call
    now = time.monotonic()
    wait = 0.1 - (now - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _get_series(series_id: str, api_key: str, limit: int = 24) -> Optional[list[dict]]:
    """Fetch the last `limit` observations (chronological) for a FRED series."""
    _throttle()
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
        "observation_start": "2000-01-01",
    }
    try:
        r = SESSION.get(FRED_BASE, params=params, timeout=20)
        if r.status_code == 200:
            return list(reversed(r.json().get("observations", [])))
        if r.status_code in (400, 401):
            # Bad/invalid key — surface once, stop hammering.
            print(f"  ⚠️  FRED {series_id}: bad/invalid API key (HTTP {r.status_code})")
            return None
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠️  FRED {series_id} request failed: {e}")
        return None
    return None


def _to_float(v: str) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


class FredError(Exception):
    pass


def get_macro_snapshot(api_key: Optional[str] = None) -> dict:
    """Return a macro snapshot dict. Reads FRED_API_KEY from env if not passed.

    With no key returns {"status": "no_key", ...} so the pipeline can skip.
    """
    api_key = api_key or os.getenv("FRED_API_KEY")
    if not api_key:
        return {
            "status": "no_key",
            "source": "FRED",
            "note": "Set FRED_API_KEY in agent/.env to enable macro enrichment",
        }

    raw: dict[str, list[dict]] = {}
    for sid in SERIES:
        obs = _get_series(sid, api_key)
        if obs:
            raw[sid] = obs

    snap: dict = {"status": "ok", "source": "FRED", "as_of": None}

    def latest(sid) -> Optional[tuple[str, float]]:
        for o in reversed(raw.get(sid, [])):
            val = _to_float(o.get("value"))
            if val is not None:
                return o.get("date"), val
        return None

    ff = latest("FEDFUNDS")
    if ff:
        snap["fed_funds_rate"], snap["as_of"] = ff[1], ff[0]
    un = latest("UNRATE")
    if un:
        snap["unemployment_rate"] = un[1]
    t10 = latest("DGS10")
    if t10:
        snap["treasury_10y"] = t10[1]
    t2 = latest("DGS2")
    if t2:
        snap["treasury_2y"] = t2[1]
    if snap.get("treasury_10y") is not None and snap.get("treasury_2y") is not None:
        snap["yield_curve_spread"] = round(snap["treasury_10y"] - snap["treasury_2y"], 3)
    gdp = latest("GDP")
    if gdp:
        snap["nominal_gdp"] = gdp[1]

    # CPI YoY (latest vs 12 months earlier)
    cpi = raw.get("CPIAUCSL", [])
    cvals = [(o["date"], _to_float(o["value"])) for o in cpi if _to_float(o["value"]) is not None]
    if len(cvals) >= 13:
        cur = cvals[-1][1]
        yr_ago = cvals[-13][1]
        if yr_ago:
            snap["cpi_yoy"] = round((cur / yr_ago - 1) * 100, 2)

    # Core PCE YoY (PCEPI)
    pce = raw.get("PCEPI", [])
    pvals = [(o["date"], _to_float(o["value"])) for o in pce if _to_float(o["value"]) is not None]
    if len(pvals) >= 13:
        cur = pvals[-1][1]
        yr_ago = pvals[-13][1]
        if yr_ago:
            snap["core_pce_yoy"] = round((cur / yr_ago - 1) * 100, 2)

    return snap


def enrich_with_macro(enriched_portfolio: dict, api_key: Optional[str] = None) -> dict:
    """Attach the macro snapshot to the enriched portfolio's top level.

    Best-effort: never raises. Returns the (mutated) dict.
    """
    try:
        snap = get_macro_snapshot(api_key)
    except Exception as e:  # noqa: BLE001 — never break the pipeline
        snap = {"status": "error", "source": "FRED", "error": str(e)}
    enriched_portfolio["macro"] = snap
    return enriched_portfolio


if __name__ == "__main__":
    import json as _json

    snap = get_macro_snapshot()
    print(_json.dumps(snap, indent=2, default=str))
