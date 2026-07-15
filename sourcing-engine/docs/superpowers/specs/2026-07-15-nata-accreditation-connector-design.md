# NATA Accreditation Connector — Design Spec

**Date:** 2026-07-15
**Status:** Approved (brainstorming), pending implementation plan
**Author:** Origo / Scout

## Context

Origo's buy-box targets founder/family-owned Australian testing, inspection,
certification, calibration, compliance, environmental, safety, and industrial-services
businesses in the $1–15M EBITDA range. NATA (National Association of Testing Authorities)
is the government-recognised accreditation body for exactly these labs. A NATA
accreditation is a **Tier-1 regulatory moat**: expensive to obtain, slow to earn, audited,
and a genuine customer lock-in. It is also a *proprietary* signal — Google Maps and Yellow
Pages can find the same company but can't tell you it holds NATA accreditation.

This spec adds NATA as both a **discovery source** (Plan A: sweep the register for
accredited private companies) and an **enrichment source** (Plan B: annotate candidates
found elsewhere with their NATA status from a local cache).

### Decisions locked during brainstorming
1. **Fetch mechanism = Apify `apify/playwright-scraper`.** The REST path is dead
   (`/wp-json/` returns **403**), and results are **JS-rendered** (static HTML shows
   "0 results", no `/site/` links) — so `apify/rag-web-browser` (no JS) won't work either.
2. **Classifier default = local Ollama `qwen2.5:3b`** (pulled during build), kept
   **pluggable** via `classifier_provider: ollama | anthropic`.
3. **Fully wired ON** — registered `enabled: true`, tiled in the orchestrator, Plan B active
   in `EnrichmentNode` — **reconciled with "don't break the engine" via strict graceful
   degradation**: every NATA failure path is non-fatal (see §Safety).
4. **Correct base class = `ScrapeConnector`** (not `AgentConnector`). `AgentConnector` is
   hardwired to rag-web-browser + LLM extraction; `playwright-scraper` returns structured
   rows via a pageFunction, which is the `ScrapeConnector` contract (`actor_id`,
   `build_input`, `normalize`, cached `_run_actor`).

## The critical distinction — sites vs parent organisations

NATA lists **sites** (individual accredited facilities), not parent organisations. One
company appears once per accredited site (NSW Health Pathology has 60+ site rows). Two
consequences:

- **Aggregation is mandatory** — the raw scrape is per-site; the connector aggregates to one
  `CompanyRecord` per parent, rolling up site count + accreditation numbers as evidence.
- **The parent set mixes private and public entities** — public hospitals, state pathology
  services, universities, CSIRO, and agencies all hold NATA accreditation and are **not**
  acquisition candidates. The connector must filter to **private commercial entities only**.
  That fuzzy classification is the local model's one job; everything else is deterministic.

## Architecture

```
buybox → planning → acquiring:
  ├─ google_maps / yellow_pages / telstra_awards   (existing)
  └─ nata_accreditation                            (NEW, ScrapeConnector)
         ├─ fetch:      Apify playwright-scraper, tiled per state × include-keyword
         ├─ aggregate:  _group_by_parent (name-normalised, same as entity resolver)
         ├─ classify:   ownership classifier → keep only private_commercial
         └─ normalize:  one CompanyRecord per surviving parent
resolving:  EntityResolver anchors name+state → ABN
enriching:  Plan B — guarded lookup in nata_parents cache annotates OTHER candidates
ranking → shortlist-gate → complete   (judge reads NATA fields as strong standouts)
```

### Components (new)
- **`src/sourcing/connectors/nata.py` — `NATAConnector(ScrapeConnector)`**
  - `actor_id = "apify/playwright-scraper"`.
  - `_build_url(state, search, filter_by="service", status="active", page=1)` — §3.1.
  - `build_input(params)` — URL list, wait selector `div:has-text("results")`, and the
    pageFunction that waits for the results grid, reads the total-results count, and extracts
    per-card: parent org, site name, accreditation & site numbers (regex), address. Selectors
    match on **class-contains + structural relationships**, not exact class names (robust to
    Tailwind churn / `bis_skin_checked`).
  - `fetch(params)` — runs the tile, paginates (20/page assumed, verify live; hard cap **25
    pages** per tile → warn+truncate), dedupes by `(accreditation_number, site_number)`.
  - `_group_by_parent(raw)` — keys on normalised parent name (strip legal suffixes, casefold,
    collapse whitespace — reuse the entity resolver's normalizer), rolls up sites,
    accreditation numbers, states, service types.
  - `normalize(parent)` — runs the classifier, emits one `CompanyRecord` per
    `private_commercial` parent (see §CompanyRecord). Classifier failure ⇒ **return no
    records** (warn), never raise.
  - `load()` + `NATACache` — writes/reads `data/nata.duckdb` `nata_parents` table;
    `find_by_normalized_name(name, state)` powers Plan B.

- **`src/sourcing/classifiers/ownership_classifier.py`**
  - Pluggable client: `provider ∈ {ollama, anthropic}`; default `ollama`/`qwen2.5:3b`.
  - Prompt from plan §5.2 (5 categories: private_commercial, public_sector, non_profit,
    listed_or_multinational, unclear). Only `private_commercial` passes.
  - **Batch of 10** (JSON array in/out, same order); on order-mismatch fall back to per-item.
  - Confidence bands (§5.4): `≥0.8` trust; `0.5–0.8` keep + flag
    `nata_classification_uncertain`; `<0.5` or `unclear` drop + flag
    `nata_classification_low_confidence`. JSON parse failure → retry once → else `unclear`.
  - Runs on the **aggregated parent list** (hundreds), not per-site rows — bounds cost.

### Changes to existing files (all additive)
- **`models/company.py`** — six new **optional** `MoatSignals` fields with defaults:
  `nata_accreditation: bool = False`, `nata_site_count: int | None`,
  `nata_service_types: list[str] = []`, `nata_accreditation_numbers: list[str] = []`,
  `nata_states: list[str] = []`, `nata_multistate: bool = False`.
- **`orchestrator.py`** — add `nata_accreditation` to `_TILED_SOURCES`; add a NATA branch in
  `params_for_connector` producing state × include-keyword tiles.
- **`enrichment/enrichment_node.py`** — one **guarded, non-fatal** Plan B call after ABN
  resolution: look up the resolved legal name in `NATACache`; if hit, set the six NATA fields
  + `regulatory_accreditation = True` and append a `nata_cache` provenance entry.
- **`data/source_registry.yaml`** — new `nata_accreditation` entry
  (`connector_type: scrape`, `actor_id: apify/playwright-scraper`, `gate: full_pool`,
  `tiled_per_state: true`, `enabled: true`, the seven `fields_provided`).
- **`config.py`** — `classifier_provider`, `classifier_model` (`qwen2.5:3b`),
  `classifier_ollama_url`, `classifier_timeout_seconds`.

## CompanyRecord produced (per surviving parent)
- `legal_name` = parent org (entity resolver may re-normalise to ASIC legal name post-ABN).
- `location.state` = primary state (highest site count); all states in `moat_signals.nata_states`.
- `sector.category_text` seeded from matched service types.
- `moat_signals.regulatory_accreditation = True`, `nata_accreditation = True`, + the five
  NATA-specific fields; `nata_multistate = len(nata_states) > 1`.
- Provenance: source `"nata"`, locator `"Accreditation #<n> + N others"`, confidence `0.95`.
- `abn = None`, `resolution_confidence = 0.0` — resolved downstream. Expect ~85% resolution;
  some (charities/co-ops without clean legal names) land in `abn_match_uncertain`.

## Safety — how "fully wired ON" honors "don't break the engine"

Every NATA failure path is **non-fatal**:
- **Acquiring** — the orchestrator already wraps each connector's `fetch`/`normalize` in
  `try/except` (`orchestrator.py` `fetch_all`) and warns+continues. NATA inherits this; an
  Apify 403/cap simply drops NATA's rows for that run. **This isolation is not modified.**
- **Classifier down / Ollama unreachable** — `normalize()` catches it and returns **zero**
  NATA records (warn), rather than raising. NATA contributes nothing; the run proceeds.
- **Plan B** — the `EnrichmentNode` lookup is wrapped so a missing `nata_parents` table or a
  query error is a **silent no-op**; it never raises into the per-record enrich loop.
- **Cache isolation** — NATA uses its **own `data/nata.duckdb`**; the ASIC `bulk.duckdb` is
  never touched.
- **Model additivity** — the six `MoatSignals` fields are optional with defaults; existing
  records and the scoring/judge paths are unaffected (the judge already reads
  `regulatory_accreditation`; the new fields add granularity only).
- **Degradation check** — 0 records extracted with a non-zero `totalResults` ⇒ warn (possible
  site-structure change), don't silently pass.

## Testing

**Offline unit tests (default suite, no network):**
- Extraction from a captured `playwright-scraper` fixture (per-site rows).
- `_group_by_parent` aggregation (name-normalisation, multi-state rollup, dedupe).
- Ownership classifier with a **mocked** client: category mapping, confidence bands, batch
  order-mismatch fallback, JSON-retry.
- `normalize()` keeps only `private_commercial`; classifier-failure ⇒ empty + warn.
- `NATACache` write/read + `find_by_normalized_name`; Plan B annotates a candidate and no-ops
  when the table is absent.
- MoatSignals field population + `nata_multistate` logic.
- Registry: `nata_accreditation` resolves to `ScrapeConnector` (existing `test_registry`
  invariant).

**Live integration tests (`@pytest.mark.integration`, excluded from default run):**
- One NSW `s=testing` Apify sweep — asserts extraction + aggregation + classifier filtering.
  Gated on `APIFY_API_TOKEN`; **note: spends Apify credits** (account is near its cap).
- Classifier live test against `qwen2.5:3b` — gated on Ollama + model presence; skips cleanly
  otherwise.

## Cost bounds
- **Apify** — ~$0.005/page. Filtered sweep ≈ 8 keywords × 5 states × ~2 pages ≈ $0.40/run;
  cached weekly so repeat runs approach $0. (Account is near its monthly cap — live sweeps
  should be run deliberately, not on every dev run; unit tests never hit Apify.)
- **Classifier** — local `qwen2.5:3b`, effectively $0; ~hundreds of parents, batched 10s.

## Out of scope (YAGNI)
- Lapsed-accreditation history (`status=active` only; a `check_lapsed(abn)` diligence hook is
  future work).
- Reusing the classifier for entity-resolution tie-breaks / address parsing (§5.6) — noted,
  not built.
- The monthly broad-sweep scheduler — the connector supports a blank-`s` broad sweep, but
  cadence/scheduling is not built here.

## Build order (for the implementation plan)
1. Pull `qwen2.5:3b`; add `classifier_*` settings.
2. Six `MoatSignals` fields (+ existing model tests still green).
3. Ownership classifier module + offline unit tests (mocked client).
4. `NATAConnector` skeleton on `ScrapeConnector` + `_build_url`/`build_input`.
5. Extraction + `_group_by_parent` + `normalize` + offline fixture tests.
6. `NATACache` (`data/nata.duckdb`) + `load()` + `find_by_normalized_name`.
7. Registry entry; `_TILED_SOURCES` + `params_for_connector` NATA tiling.
8. Guarded Plan B in `EnrichmentNode` + tests.
9. Live integration tests (gated). Full offline suite green + ruff clean.
