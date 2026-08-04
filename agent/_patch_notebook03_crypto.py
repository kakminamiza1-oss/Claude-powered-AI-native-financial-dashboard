#!/usr/bin/env python3
"""Inject crypto tracking into notebook 03 (idempotent).

Adds a markdown + code cell (after the FRED cell) that attaches live crypto
quotes (free CoinGecko) to enriched_portfolio via crypto_tracker.enrich_with_crypto().
The user's CoinDesk MCP can later augment/replace this free baseline.
"""
import json
import os

NB = os.path.join(os.path.dirname(__file__), "notebooks", "03_data_enrichment.ipynb")
FRED_MARKER = "# ── FRED MACRO ──"
CRYPTO_MARKER = "# ── CRYPTO ──"

with open(NB, "r", encoding="utf-8") as f:
    nb = json.load(f)

src_all = "".join("".join(c.get("source", [])) for c in nb["cells"])


def code(src):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": src.splitlines(keepends=True)}


def md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)}


if CRYPTO_MARKER not in src_all:
    crypto_md = md("## 6c. Crypto Tracking (CoinGecko — free)\n\n"
                   "Live BTC/ETH/SOL (and any held coin) prices + 24h change from the free "
                   "CoinGecko API. Attaches to `enriched_portfolio['crypto']`. The user's "
                   "CoinDesk MCP can later augment this with richer data.\n")
    crypto_code = code(
        f'{CRYPTO_MARKER}\n'
        'import sys\n'
        'sys.path.insert(0, os.path.join(".."))\n'
        'from crypto_tracker import enrich_with_crypto\n'
        'CRYPTO_ENABLED = os.environ.get("ENABLE_CRYPTO", "1") != "0"\n'
        'if CRYPTO_ENABLED:\n'
        '    try:\n'
        '        enriched_portfolio = enrich_with_crypto(enriched_portfolio)\n'
        '        c = enriched_portfolio.get("crypto", {})\n'
        '        if c.get("status") == "ok":\n'
        '            q = c.get("quotes", {})\n'
        '            print(f"\\nCRYPTO quotes: " + ", ".join(\n'
        '                f"{s}=${v[\'price_usd\']} ({v[\'change_24h_pct\']}%)" for s, v in q.items()))\n'
        '        else:\n'
        '            print(f"\\nCRYPTO: {c.get(\'status\')}")\n'
        '    except Exception as e:\n'
        '        print(f"\\nCRYPTO error (non-fatal): {e}")\n'
        'else:\n'
        '    print("\\nCRYPTO disabled (ENABLE_CRYPTO=0)")\n'
    )
    at = next((i for i, c in enumerate(nb["cells"])
               if FRED_MARKER in "".join(c.get("source", []))), len(nb["cells"]))
    nb["cells"][at + 1:at + 1] = [crypto_md, crypto_code]
    with open(NB, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1, ensure_ascii=False)
    print("Injected CRYPTO cell into notebook 03.")
else:
    print("CRYPTO cell already present.")
