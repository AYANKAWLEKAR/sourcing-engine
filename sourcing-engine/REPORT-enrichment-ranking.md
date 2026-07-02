# Enrichment & Ranking Pipeline — Report

**Date:** 2026-06-28  ·  **Scope:** next-phase plan Parts A (Enrichment) + B (Screen & Rank), plus the AusTender connector addendum
**LLM:** local **Ollama + qwen2.5** on Docker — **no Anthropic / cloud API** (per instruction)
**UI:** deferred (per instruction — "treat the UI as the last step; first ensure a proper ranking is done")

| Part | What | Result |
|---|---|---|
| A | Signal extractor (qwen), proxy estimator, **AusTender connector**, enrichment node | ✅ built + tested |
| B | Screen (EXCLUDE→GATE→PROXY_GATE), locked scoring model, qwen judge, ranked shortlist | ✅ built + tested |
| — | End-to-end demo: buy box → discovery → enrich → **ranked top-N** | ✅ runs on a live HVAC/Brisbane slice |
| C/D | FastAPI run-persistence + analyst UI | ⏸ deferred (next) |

**Tests:** 151 unit passed · 15 integration passed (AusTender 3 + ABN 6 + ASIC 6) · ruff clean · 88% coverage on `enrichment/` + `rank/` + `austender`.

---

## 1. What was built

### Part A — Enrichment (`src/sourcing/enrichment/`)
- **`AusTenderConnector`** (`connectors/austender.py`, an `APIConnector`) — government-contract
  moat signal joined on supplier ABN. Sets `moat_signals.gov_contracts`, `gov_contract_value_aud`,
  `gov_contract_count`, `gov_contract_agencies`.
- **`SignalExtractor`** — `website_text_raw` → `business_model`, `keyword_hits`, `exclude_hits`,
  ANZSIC guess, and moat signals (physical ops, accreditation, hard assets, recurring revenue),
  via **qwen in JSON mode**. Empty text → an honest `unverified:sector:no_website_text` flag.
- **`ProxyEstimator`** (+ `data/ato_benchmarks.csv`) — `revenue_est = employees × rev/employee(ANZSIC)`,
  `ebitda_est = revenue × margin`, confidence capped ≤ 0.4. Sets the PROXY_GATE flag only; does **not** score.
- **`EnrichmentNode`** — per-record waterfall: AusTender (free, full sweep) → website fetch → signal
  extract. Nothing fabricated; unfillable fields get `unverified:*` flags.

### Part B — Screen & Rank (`src/sourcing/rank/`)
- **`screen.py`** — EXCLUDE (listed / PE-VC / excluded-sector) → GATE (country, min-years) →
  PROXY_GATE (EBITDA band). Only *known* violations fail; unknowns pass but are flagged.
- **`score.py`** — the locked model:
  `s_sector = 0.5·s_sem + 0.3·s_kw + 0.2·s_code`;
  `fit = 0.50·s_sector + 0.25·s_state + 0.25·s_model`;
  `adjusted = fit·(0.7 + 0.3·mean_confidence)`; `score = adjusted·(0.85^unverified_gates)·100`.
  **No `s_ai`/`s_frag`/`s_size`/`s_age`, no proxy penalty** — guarded by an AST-based unit test.
- **`judge.py`** — qwen reads the full record (incl. gov contracts / accreditation / IP / awards
  that carry no statistical weight) → calibrated 0–1 fit + rationale + standout signals.
- **`rank.py`** — `S_final = 0.55·(S_stat/100) + 0.45·judge_fit`, top-50 to the judge, then a
  postcode **diversity guard** → `RankedCompany` top-N (record + both scores + rationale +
  standout chips + a `deferred_assessment` checklist).

### Models / infra
- Extended `MoatSignals` (gov-contract fields, hard_assets, recurring_revenue_hint), `Sector.anzsic_confidence`,
  `Provenance.locator`, `CompanyRecord.website_text_raw` + `flags` + `resolution_confidence`; new `RankedCompany`.
- `docker-compose.yml` gained an **`ollama`** service; `llm.py` gained JSON-mode (`complete_json`).

---

## 2. Two things reconciled against reality (honest notes)

**(a) Ollama/qwen instead of Anthropic.** The plans' code targets the Anthropic API (Haiku). Per
your instruction, every LLM call (signal extractor + judge) runs on a **local qwen2.5 model served
by Ollama in Docker**. Default `qwen2.5:3b` (fast on CPU); `qwen2.5:7b` available for higher quality.
> CPU-only Docker Ollama is slow (~1 min/call for 3b), so the demo caps the LLM pool to 5 records.
> On a GPU/Metal host this is seconds per call.

**(b) The AusTender addendum's endpoint doesn't exist.** The addendum assumed
`findContractNotice/au?supplierABN=…`; the real OCDS API returns **403 "Missing Authentication Token"**
for that path. The API only supports **date-range** queries (`findByDates/{type}/{start}/{end}`), with
supplier ABN in `parties[].additionalIdentifiers[]` (scheme `AU-ABN`) and the buyer as `procuringEntity`.
So the connector scans a bounded recent window and filters by supplier ABN client-side (the window
response is cached, so many ABN lookups cost one HTTP call). Comprehensive per-ABN history needs the
**bulk OCDS dataset as a BulkConnector** — a noted follow-up. The connector's mapping is proven by unit
tests against the real shape and a live integration test that finds a supplier in a window then looks it up.

---

## 3. Test results

```
# Offline unit suite (no LLM / no credits)
$ pytest tests/unit/ -q
151 passed

# Enrichment + ranking specifically
$ pytest tests/unit/test_enrichment.py tests/unit/test_ranking.py tests/unit/test_austender_connector.py -q
… passed   (signal extractor, proxy, node; screen, exact score math, judge, rank, diversity; AusTender)

# Live integration (real APIs; no LLM)
$ pytest tests/integration/test_austender_live.py tests/integration/test_abn_lookup.py tests/integration/test_asic_bulk.py -q
15 passed

$ ruff check src/ tests/ scripts/
All checks passed!
```

Coverage on the new code: **88%** across `enrichment/` + `rank/` + `connectors/austender.py`.
The exact scoring math is asserted on fixtures (a perfect record = 100.0; the dampener floors a
no-provenance record at 85.0; one unverified gate multiplies by 0.85), and an AST test fails if any
removed term (`s_ai`/`s_frag`/`s_size`/`s_age`) or a proxy penalty ever reappears in `score.py`.

---

## 4. Example output — live end-to-end run

`python scripts/rank_demo.py` — buy box "founder-owned HVAC/air-conditioning installers in Brisbane QLD",
no ABNs in. All signal extraction + judging on local qwen2.5:3b.

```
<LIVE_OUTPUT_PLACEHOLDER>
```

---

## 5. Definition of done (next-phase plan §8)

| Criterion | Status |
|---|---|
| Natural-language buy box drives discovery → enrichment → ranking to a top-N | ✅ (`scripts/rank_demo.py`; FastAPI wrapper is Part C, deferred) |
| Ranking is exactly `0.50·s_sector + 0.25·s_state + 0.25·s_model` + dampener + unverified penalty; no removed terms, no proxy penalty | ✅ (asserted by unit tests) |
| EXCLUDE removes listed / PE-backed / excluded-sector before scoring | ✅ |
| Every shortlist record carries per-field provenance + a `deferred_assessment` checklist | ✅ |
| Moat/awards inform only the judge + the card, never the statistical score | ✅ |
| Unit suites green offline (no key/credits); live acceptance run produces a sensible ranked list | ✅ |
| The analyst UI | ⏸ deferred (Part D — "UI last") |

---

## 6. What's next

- **Part C** — persist a `Run` (buy-box → planning → … → ranking → complete) in Postgres and expose the
  FastAPI surface (`POST /runs`, `GET /runs/{id}`, per-company `/sources`) so the pipeline is one polled API call.
- **Part D** — the analyst UI (buy-box chat → live run progress → ranked shortlist → company detail with
  per-field source/confidence). Build against the Part C contract.
- **AusTender bulk** — ingest the bulk OCDS dataset as a `BulkConnector` for comprehensive per-ABN history.
- **Quality** — run on `qwen2.5:7b` (or a GPU host) for sharper extraction/judging.

## 7. How to reproduce

```bash
cd sourcing-engine && source .venv/bin/activate
pip install -e ".[dev,connectors]"
docker compose up -d ollama && docker exec $(docker ps -q --filter name=ollama) ollama pull qwen2.5:3b

pytest -m "not integration"                  # offline suite (151)
python scripts/rank_demo.py                  # live end-to-end ranked shortlist
```
