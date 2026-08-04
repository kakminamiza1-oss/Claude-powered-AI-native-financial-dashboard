#!/usr/bin/env python3
"""Wire SEC EDGAR + FRED macro into notebook 04 (Claude analysis).

Two changes, idempotent:
  1. System prompt: tell Claude the portfolio now carries `macro` (FRED) at the
     top level and per-ticker `fundamentals.edgar` (audited SEC figures), and to
     use them in the analysis framework.
  2. Payload builder: forward enriched_portfolio["macro"] into the payload and
     stop stripping edgar fields (already passed through; this just makes sure
     `macro` is included).
"""
import json
import os

NB = os.path.join(os.path.dirname(__file__), "notebooks", "04_claude_analysis.ipynb")

with open(NB, "r", encoding="utf-8") as f:
    nb = json.load(f)

# --- 1. System prompt edits ---
for c in nb["cells"]:
    if c["cell_type"] != "code":
        continue
    src = "".join(c["source"])
    if "SYSTEM_PROMPT" in src and "Analysis Framework" in src:
        # Idempotency guard uses a token that actually appears after injection.
        if "Audited SEC fundamentals" not in src:
            c["source"] = [
                line.replace(
                    '- "enrichment": per-ticker object with technicals, fundamentals, performance, and news\n',
                    '- "enrichment": per-ticker object with technicals, fundamentals, performance, and news\n'
                    '- "macro": portfolio-level U.S. macro snapshot from FRED (fed_funds_rate, cpi_yoy, core_pce_yoy, unemployment_rate, treasury_10y, treasury_2y, yield_curve_spread, nominal_gdp). Yield-curve spread < 0 means an inverted curve (recession risk).\n',
                )
                for line in c["source"]
            ]
            # b) Extend the Analysis Framework fundamentals line + add macro line
            new_src = []
            for line in c["source"]:
                new_src.append(line)
                if line.startswith('2. **Fundamental quality**'):
                    new_src.append(
                        '2b. **Audited SEC fundamentals** — each ticker\'s fundamentals include an "edgar" block with actual filed revenue, net income, free cash flow, debt-to-assets, net margin, and ROE, plus the latest 10-K/10-Q filing dates. Prefer these over yfinance summary fields; flag if financials are stale (> ~13 months since last 10-K).\n'
                    )
                if line.startswith('5. **Portfolio context**'):
                    new_src.append(
                        '6. **Macro context** — weigh the FRED "macro" snapshot: high/inverted yield curve, rising CPI/PCE, Fed rate path, and unemployment trend all shift risk. Tighten conviction on rate-sensitive names (growth, REITs, long-duration) when the curve is inverted or inflation is hot.\n'
                    )
            c["source"] = new_src
        break

# --- 2. Payload builder: include macro ---
for c in nb["cells"]:
    if c["cell_type"] != "code":
        continue
    src = "".join(c["source"])
    if 'payload = {' in src and '"enrichment": {}' in src and "macro" not in src:
        # insert macro into the payload dict
        c["source"] = [
            line.replace(
                '    "enrichment": {},\n',
                '    "enrichment": {},\n    "macro": enriched.get("macro", {}),\n',
            )
            for line in c["source"]
        ]
        break

with open(NB, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print("Wired EDGAR + FRED macro into notebook 04.")
