# Agent Connector — Telstra Best of Business Awards — Report

**Date:** 2026-07-01  ·  **Scope:** the first concrete `AgentConnector` (a niche discovery source)
**LLM:** local Ollama + qwen2.5:3b on Docker — no cloud API
**Result:** ✅ built, unit-tested, and live-verified end to end (39 finalists → 95% resolved to ABNs)

## What this delivers

The connector hierarchy had four working mechanisms (Bulk, API, Scrape, + the enrichment/ranking
pipeline) but **zero concrete `AgentConnector`s** — the "fetch a page + LLM-extract" path. This
adds the first one: **`TelstraAwardsConnector`** (`connectors/awards.py`), a *discovery* source that
sweeps Telstra Best of Business finalist pages and yields a curated pool of pre-vetted quality SMBs,
each carrying `moat_signals.award_finalist=True`. The ranker's judge already weighs that signal, so
it flows into scoring and the shortlist card with no downstream changes.

| Piece | Status |
|---|---|
| `base_agent._default_extractor` fix (was stale: no `system`, wrong model, no JSON) → `complete_json` + qwen | ✅ |
| `AwardSignal` model + `CompanyRecord.award_signals` | ✅ |
| `AwardRegisterConnector` base + `TelstraAwardsConnector` | ✅ |
| Registry entry `telstra_awards` (agent, `connector_built: true`) | ✅ |
| Offline unit tests (9) + live integration test + `scripts/awards_demo.py` | ✅ |

**Tests:** 161 unit passed · ruff clean.

## Design (tuned for a local CPU model)

- **name + state** are pulled **structurally** from the page (`#### {name}` / `{state} Finalist`
  blocks) — verbatim facts, provenance confidence **0.9**.
- **category** is determined by **one plain-text qwen call** classifying every finalist's business
  sector (per your direction — LLM-classified, not URL-slug-mapped). It feeds `sector.category_text`,
  provenance confidence **0.5**.

### The key finding: `format="json"` is the CPU bottleneck

The first attempts timed out (>600s). Root cause: **grammar-constrained JSON decoding** (`format="json"`)
is pathologically slow on CPU for a ~40-item array. The *same* extraction as **plain text** finished
in ~250–550s. So the connector uses `complete_text` (new in `llm.py`) and parses a numbered list;
JSON mode is kept only for small single-object outputs (the signal extractor, the judge). The
`ollama_timeout` config default was raised to 900s.

## Live proof — extraction (`scripts` smoke, one 2025 category page)

39 finalists extracted; names/states structural, categories qwen-classified:

```
[fetch+classify] 547s  finalists=39
  Australian Scaffold & Access     NSW  cat=['scaffolding']
  Veterinary Specialists of Sydney NSW  cat=['veterinary care']
  QMS NDT & NACE                   SA   cat=['non-destructive testing']
  Genr8 Energy                     TAS  cat=['renewable energy']
  Phronesis Security               VIC  cat=['cyber security']
  MyEnergy Engineering             SA   cat=['engineering']
  … (34 more)
```

## Live proof — end to end (`python scripts/awards_demo.py`)

No ABNs in → award-anchored, ranked records out:

```
1. Sweep finalist pages ['embracing-innovation'] (rag-web-browser → qwen)…
   39 finalists extracted (all carry award_finalist=True, no ABN)
2. Resolve finalist names → ABN spine (name + state, no postcode)…
   resolved 37/39 (95%) to an ABN

   finalist                           state abn           award
   INDUSTRY WASTE RECOVERY PTY LTD    ACT   70621839585   True   (Maps name "Big Bag Recovery")
   AUSTRALIAN SCAFFOLD & ACCESS PTY   NSW   31154065960   True
   VETERINARY SPECIALISTS OF SYDNEY   NSW   16628288595   True
   BRIGHT IDEA NUTRITION PTY LTD      NSW   83616555134   True   (trading name "SAVVY Beverages")
   … 37/39 anchored

3. Rank (qwen judge weighs the award-finalist signal)…
    #  company                          standout signals
    1  KITBAG CONSULTING PTY LTD        award finalist
    2  AUSTRALIAN SCAFFOLD & ACCESS     award finalist
```

**95% resolution** — much better than the plan's tempered expectation, because the ABN register maps
trading/display names to legal entities (e.g. "SAVVY Beverages" → "BRIGHT IDEA NUTRITION PTY LTD").
Every resolved finalist carries the tier-1 `award_finalist` signal into ranking as a standout chip.

## Honest notes

- **Category non-determinism (fixed):** in one demo run qwen prefixed the business name to its
  category ("Big Bag Recovery — environmental" instead of "environmental"). Added `_clean_category`
  (strips an echoed name/separator) + unit tests; the clean smoke run above shows the intended output.
- **Judge scores were low on CPU** in the demo (broad "innovative SMBs" buy box + JSON-mode judge on
  CPU). The award signal still surfaces as a standout chip; a focused buy box + `qwen2.5:7b`/GPU would
  sharpen the judge. This is a pre-existing ranking behavior, not specific to this connector.
- **Latency:** ~250–550s per page on CPU-only Docker qwen (cached 14 days). A GPU/Metal host makes
  this seconds.

## What's next

- The other award registers (Trades Champion, Local Business Awards) reuse `AwardRegisterConnector` —
  set `program`/`base_url_template`/`category_slugs`.
- MCP connector (Inven); Part C (FastAPI run persistence); Part D (analyst UI).

## Reproduce

```bash
cd sourcing-engine && source .venv/bin/activate
pytest tests/unit/test_awards_connector.py -q          # offline (9)
pytest -m integration tests/integration/test_awards_live.py   # live (Apify + qwen)
python scripts/awards_demo.py                          # sweep → resolve → rank
```
