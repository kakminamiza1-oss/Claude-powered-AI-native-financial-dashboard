#!/usr/bin/env python3
"""Inject SEC EDGAR enrichment into notebook 03_data_enrichment.ipynb.

Adds one new code cell right after the Fundamentals cell (code cell 5 /
its display cell) that:
  1. imports sec_edgar
  2. loops over every ticker and merges EDGAR data into fundamentals[ticker]
  3. prints a short summary

Also adds a short markdown header cell before it.

This is idempotent — if the EDGAR marker is already present it does nothing.
"""
import json
import os

NB = os.path.join(os.path.dirname(__file__), "notebooks", "03_data_enrichment.ipynb")
MARKER = "# ── EDGAR ──"


def new_code_cell(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def new_md_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


with open(NB, "r", encoding="utf-8") as f:
    nb = json.load(f)

# Idempotency check
for cell in nb["cells"]:
    if MARKER in "".join(cell.get("source", [])):
        print("EDGAR cell already present — nothing to do.")
        break
else:
    edgar_md = new_md_cell(
        "## 3b. SEC EDGAR Fundamentals (official audited data)\n"
        "\n"
        "Pulls actual filed 10-K / 10-Q numbers (revenue, net income, EPS, "
        "balance-sheet totals, operating cash flow) straight from the SEC's "
        "free EDGAR API. These are the *real* audited figures — a useful "
        "cross-check and depth boost on top of yfinance's summary fields.\n"
    )
    edgar_code = new_code_cell(
        f'{MARKER}\n'
        'import sys\n'
        'sys.path.insert(0, os.path.join(".."))  # agent/ root holds sec_edgar.py\n'
        'from sec_edgar import enrich_fundamentals_with_edgar\n'
        '\n'
        'EDGAR_ENABLED = os.environ.get("ENABLE_EDGAR", "1") != "0"\n'
        'edgar_summary = {}\n'
        'if EDGAR_ENABLED:\n'
        '    for t in list(fundamentals.keys()):\n'
        '        try:\n'
        '            fundamentals[t] = enrich_fundamentals_with_edgar(t, fundamentals[t])\n'
        '            ed = fundamentals[t].get("edgar", {})\n'
        '            if ed.get("status") == "not_found":\n'
        '                edgar_summary[t] = "not_found (likely fund/ADR/foreign)"\n'
        '            elif "edgar_error" in fundamentals[t]:\n'
        '                edgar_summary[t] = f"error: {fundamentals[t][\'edgar_error\'][:60]}"\n'
        '            else:\n'
        '                rev = ed.get("revenue")\n'
        '                ni = ed.get("net_income")\n'
        '                fcf = ed.get("free_cash_flow_edgar")\n'
        '                edgar_summary[t] = f"rev={rev} ni={ni} fcf={fcf}"\n'
        '        except Exception as e:\n'
        '            edgar_summary[t] = f"exception: {e}"\n'
        '        time.sleep(0.1)  # polite to SEC\n'
        '    print(f"\\n🏛️  SEC EDGAR enrichment complete for {len(edgar_summary)} tickers")\n'
        '    for t, s in edgar_summary.items():\n'
        '        print(f"   {t}: {s}")\n'
        'else:\n'
        '    print("🏛️  SEC EDGAR disabled (ENABLE_EDGAR=0)")\n'
    )

    # Insert after the fundamentals display cell (code cell 6). Find the
    # "Fundamentals fetched for" print cell index.
    insert_at = None
    for i, cell in enumerate(nb["cells"]):
        src = "".join(cell.get("source", []))
        if "Fundamentals fetched for" in src and cell["cell_type"] == "code":
            insert_at = i + 1
            break
    if insert_at is None:
        # Fallback: insert before the "Fetch Recent News" markdown.
        for i, cell in enumerate(nb["cells"]):
            if "Fetch Recent News" in "".join(cell.get("source", [])):
                insert_at = i
                break
    if insert_at is None:
        insert_at = len(nb["cells"])

    nb["cells"][insert_at:insert_at] = [edgar_md, edgar_code]
    with open(NB, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1, ensure_ascii=False)
    print(f"✅ Injected EDGAR cells at index {insert_at}")
