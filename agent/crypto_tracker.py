#!/usr/bin/env python3
"""Crypto price tracking for the financial-analyst-agent pipeline.

The dashboard is brokerage/equity-focused, but many portfolios also hold
crypto. This module pulls live BTC/ETH (and any coin the user holds) prices
and 24h change from the **free, no-key** CoinGecko public API, so crypto can
be surfaced alongside equities.

Note: the user also has a **CoinDesk MCP** connection available. When that
MCP is wired into the Claude client it can *replace/augment* this adapter with
richer CoinDesk data — this module is the zero-cost baseline that works with
no extra setup.

Free tier caveats (CoinGecko public API):
  - ~10-30 calls/min depending on load; we cache + serialize with a small
    sleep so a personal portfolio never trips the limit.
  - No key needed. If you later add a CoinGecko Demon key, set
    COINGECKO_API_KEY to raise the limit.

Usage:
    from crypto_tracker import get_crypto_quotes, enrich_with_crypto
    quotes = get_crypto_quotes(["BTC", "ETH"])   # or coin ids
    enriched = enrich_with_crypto(enriched_portfolio, holdings_with_crypto)
"""

from __future__ import annotations

import os
import time
from typing import Optional

import requests

BASE = "https://api.coingecko.com/api/v3"

# Our portfolio uses ticker symbols; map to CoinGecko ids.
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "BITCOIN": "bitcoin",
    "ETH": "ethereum",
    "ETHEREUM": "ethereum",
    "SOL": "solana",
    "SOLANA": "solana",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "XRP": "ripple",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "BNB": "binancecoin",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": os.environ.get("SEC_USER_AGENT", "hermes-financial-dashboard (local-agent@example.com)"),
})
if os.getenv("COINGECKO_API_KEY"):
    SESSION.headers["x-cg-demo-api-key"] = os.getenv("COINGECKO_API_KEY")

_last = 0.0


def _throttle():
    global _last
    now = time.monotonic()
    wait = 2.0 - (now - _last)  # be gentle: ~1 call / 2s
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def _resolve_ids(symbols: list[str]) -> dict[str, str]:
    """Map user symbols -> CoinGecko ids. Returns {symbol: id} for known ones."""
    out = {}
    for s in symbols:
        s = s.upper().strip()
        if s.lower() in SYMBOL_TO_ID.values():
            # already an id
            out[s] = s.lower()
        elif s in SYMBOL_TO_ID:
            out[s] = SYMBOL_TO_ID[s]
    return out


def get_crypto_quotes(symbols: list[str]) -> dict[str, dict]:
    """Return {symbol: {price_usd, change_24h_pct, id, source}} for known coins."""
    ids = _resolve_ids(symbols)
    if not ids:
        return {}
    id_list = ",".join(sorted(set(ids.values())))
    _throttle()
    try:
        r = SESSION.get(
            f"{BASE}/simple/price",
            params={
                "ids": id_list,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"  ⚠️  CoinGecko request failed: {e}")
        return {}

    # reverse map id -> symbol(s)
    id_to_symbols: dict[str, list[str]] = {}
    for sym, cid in ids.items():
        id_to_symbols.setdefault(cid, []).append(sym)

    out: dict[str, dict] = {}
    for cid, blob in data.items():
        price = blob.get("usd")
        chg = blob.get("usd_24h_change")
        for sym in id_to_symbols.get(cid, []):
            out[sym] = {
                "price_usd": price,
                "change_24h_pct": round(chg, 2) if chg is not None else None,
                "id": cid,
                "source": "CoinGecko (free)",
            }
    return out


def enrich_with_crypto(enriched_portfolio: dict, crypto_holdings: Optional[list[dict]] = None) -> dict:
    """Attach crypto quotes to the enriched portfolio.

    crypto_holdings: list of {"symbol": "BTC", "quantity": 0.5, "value": ...}
    If None, we scan existing holdings for known crypto symbols.
    """
    # Gather candidate symbols from holdings if not provided.
    if crypto_holdings is None:
        crypto_holdings = []
        for h in enriched_portfolio.get("holdings", []):
            sym = (h.get("ticker") or "").upper()
            if sym in SYMBOL_TO_ID:
                crypto_holdings.append(h)

    symbols = [h.get("ticker", "").upper() for h in crypto_holdings if h.get("ticker")]
    quotes = get_crypto_quotes(symbols) if symbols else {}

    crypto_block = {"status": "ok" if quotes else "none", "source": "CoinGecko (free)", "quotes": quotes}
    enriched_portfolio["crypto"] = crypto_block
    return enriched_portfolio


if __name__ == "__main__":
    import json as _json

    q = get_crypto_quotes(["BTC", "ETH", "SOL"])
    print(_json.dumps(q, indent=2))
