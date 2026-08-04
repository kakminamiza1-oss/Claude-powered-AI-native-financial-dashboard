#!/usr/bin/env python3
"""Inject FRED macro enrichment into notebook 03 (idempotent).

Adds:
  1. A markdown + code cell (after the EDGAR cell) that attaches the FRED
     macro snapshot to enriched_portfolio via fred_macro.enrich_with_macro().
  2. Hooks the macro enrichment into the export block (before the # Save line)
     so enriched_portfolio.json carries the macro context for Claude.

Also re-injects the EDGAR cell if missing (defensive — keeps this script the
single source of truth for both free enrichments).
"""
import json
import os

NB = os.path.join(os.path.dirname(__file__), "notebooks", "03_data_enrichment.ipynb")
EDGAR_MARKER = "# ── EDGAR ──"
FRED_MARKER = "# ── FRED MACRO ──"


def code(src):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": src.splitlines(keepends=True)}


def md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)}


with open(NB, "r", encoding="utf-8") as f:
    nb = json.load(f)

src_all = "".join("".join(c.get("source", [])) for c in nb["cells"])

# --- 1. EDGAR cell (re-inject if absent) ---
if EDGAR_MARKER not in src_all:
    edgar_code = code(
        f'{EDGAR_MARKER}\n'
        'import sys\n'
        'sys.path.insert(0, os.path.join(".."))\n'
        'from sec_edgar import enrich_fundamentals_with_edgar\n'
        'EDGAR_ENABLED = os.environ.get("ENABLE_EDGAR", "1") != "0"\n'
        'edgar_summary = {}\n'
        'if EDGAR_ENABLED:\n'
        '    for t in list(fundamentals.keys()):\n'
        '        try:\n'
        '            fundamentals[t] = enrich_fundamentals_with_edgar(t, fundamentals[t])\n'
        '            ed = fundamentals[t].get("edgar", {})\n'
        '            edgar_summary[t] = ("not_found" if ed.get("status") == "not_found"\n'
        '                                else f"err: {fundamentals[t].get(\'edgar_error\', \'\')[:50]}"\n'
        '                                if "edgar_error" in fundamentals[t] else\n'
        '                                f"rev={ed.get(\'revenue\')} fcf={ed.get(\'free_cash_flow_edgar\')}")\n'
        '        except Exception as e:\n'
        '            edgar_summary[t] = f"exception: {e}"\n'
        '        time.sleep(0.1)\n'
        '    print(f"\\nEDGAR enrichment done for {len(edgar_summary)} tickers")\n'
        '    for t, s in edgar_summary.items():\n'
        '        print(f"   {t}: {s}")\n'
        'else:\n'
        '    print("EDGAR disabled (ENABLE_EDGAR=0)")\n'
    )
    # insert after the "Fundamentals fetched for" code cell
    at = next((i for i, c in enumerate(nb["cells"])
               if "Fundamentals fetched for" in "".join(c.get("source", [])) and c["cell_type"] == "code"), len(nb["cells"]))
    nb["cells"][at + 1:at + 1] = [md("## 3b. SEC EDGAR Fundamentals (official audited data)\n\n"
                                     "Pulls actual filed 10-K / 10-Q numbers from the free SEC EDGAR API.\n"), edgar_code]
    src_all = "".join("".join(c.get("source", [])) for c in nb["cells"])

# --- 2. FRED cell (after EDGAR) ---
if FRED_MARKER not in src_all:
    fred_md = md("## 6b. Macro Context (FRED — free)\n\n"
                 "Attaches U.S. macro indicators (Fed Funds Rate, CPI/PCE inflation, "
                 "unemployment, Treasury yields, yield-curve spread, GDP) from the free "
                 "FRED API so Claude can frame each pick in the current economic backdrop.\n")
    fred_code = code(
        f'{FRED_MARKER}\n'
        'import sys\n'
        'sys.path.insert(0, os.path.join(".."))\n'
        'from fred_macro import enrich_with_macro\n'
        'FRED_ENABLED = os.environ.get("ENABLE_FRED", "1") != "0"\n'
        'if FRED_ENABLED:\n'
        '    try:\n'
        '        enriched_portfolio = enrich_with_macro(enriched_portfolio)\n'
        '        m = enriched_portfolio.get("macro", {})\n'
        '        if m.get("status") == "ok":\n'
        '            print(f"\\nFRED macro attached (as of {m.get(\'as_of\')}): "\n'
        '                  f"FFR={m.get(\'fed_funds_rate\')}%, CPI YoY={m.get(\'cpi_yoy\')}%, "\n'
        '                  f"curve10-2={m.get(\'yield_curve_spread\')}%, unemp={m.get(\'unemployment_rate\')}%")\n'
        '        else:\n'
        '            print(f"\\nFRED macro skipped: {m.get(\'status\')} ({m.get(\'note\', m.get(\'error\', \'\'))})")\n'
        '    except Exception as e:\n'
        '        print(f"\\nFRED macro error (non-fatal): {e}")\n'
        'else:\n'
        '    print("\\nFRED disabled (ENABLE_FRED=0)")\n'
    )
    # insert after the EDGAR code cell
    at = next((i for i, c in enumerate(nb["cells"])
               if EDGAR_MARKER in "".join(c.get("source", []))), len(nb["cells"]))
    nb["cells"][at + 1:at + 1] = [fred_md, fred_code]

with open(NB, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print("Injected FRED (and EDGAR if missing) into notebook 03.")
