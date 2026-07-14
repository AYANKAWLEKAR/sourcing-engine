# Origo Off-Market Sourcing — Pipeline Reference

A single-page map of the whole engine: what the demo does, every data source it
pulls from, how records flow through the pipeline, and exactly how the ranking works.

> **One-line summary:** a natural-language buy box goes in; a ranked, explainable
> shortlist of off-market Australian acquisition targets comes out — each with
> provenance receipts and open diligence questions. All LLM work runs on Claude
> (agent = Opus, enrichment + judge = Haiku); the statistical scoring is fully local.

---

## 1. What the demo does

The analyst UI (`python cli.py serve --ui`, Streamlit) is a single chat surface:

1. **Describe a buy box** in plain English — e.g. *"Founder-owned HVAC installers in
   Sydney, $1–5M EBITDA."*
2. The **Buy-Box Agent** (Claude Opus, a bounded tool-use loop) turns it into a
   validated `FilterRuleset`: it resolves the **sector** (→ ANZSIC codes + keywords),
   the **geography** (→ states + postcodes), and applies any size/age/ownership
   overrides you state. It narrates the ruleset back every turn and lists what's still
   missing before it can finalize.
3. On confirmation the **run pipeline** launches and streams a **verbose stage trace**
   (planning → acquiring → resolving → enriching → ranking) with live candidate counts.
4. The result is a **ranked shortlist** rendered inline: each company shows `S_final`,
   `S_stat`, the LLM judge's fit + rationale, standout signals, and a detail drawer with
   **per-field provenance** (which source filled each field, at what confidence).

A **demo-prompt cache** (see §7) replays a captured run for the canonical HVAC-Sydney
prompt in seconds instead of minutes, so live demos don't wait on scraping + LLMs.

---

## 2. The pipeline at a glance

The engine is a linear, persisted pipeline. Every stage boundary writes status to the
run store, so progress is observable (that's what the UI trace polls).

```
  buybox ─▶ planning ─▶ acquiring ─▶ resolving ─▶ enriching ─▶ ranking ─▶ complete
   │          │           │            │            │            │
   │          │           │            │            │            └─ screen → score → judge → blend → diversify
   │          │           │            │            └─ AusTender + IPGOD + ASX + website→signals (per record)
   │          │           │            └─ name → ABN (ABN Lookup + ASIC/ABR spine merge)
   │          │           └─ scrape + award-register discovery (dedup)
   │          └─ RAG source plan (rank sources by relevance + field coverage)
   └─ Buy-Box Agent conversation (Claude) → confirmed FilterRuleset
```

| Stage | Module | What happens |
|---|---|---|
| **buybox** | `agent/buybox_agent.py` | Multi-turn Claude tool loop → confirmed `FilterRuleset` (sector, geography, filters). The only interactive stage. |
| **planning** | `rag/retriever.py` | RAG over the Source Registry: rank sources by vector similarity to the buy box + field coverage, enforce invariants (spine present, ≥1 text source present) → a `SourcePlan`. |
| **acquiring** | `orchestrator.py` | Run each discovery source in the plan (scrape + award registers), tiling scrape sources per state; aggregate raw candidates; dedup pre-resolution. |
| **resolving** | `enrichment/entity_resolution.py` | For each ABN-less record: name-match via ABN Lookup, re-rank, accept ≥ 0.85, merge the ASIC/ABR spine (ACN, register name, dates). Dedup by ABN. |
| **enriching** | `enrichment/enrichment_node.py` | Per resolved record: ASX-listed check, IPGOD IP moat, AusTender gov-contract moat, website fetch → LLM signal extraction (sector/model/moat). |
| **ranking** | `rank/rank.py` | Screen → statistical score → LLM judge → blend → postcode-diversity guard. Then the **shortlist gate** enriches the top-N (Inven/LinkedIn/proxy). |
| **complete** | `runs/store.py` | Shortlist + coverage persisted; UI renders results. `failed` on any exception, with `{stage}: {error}`. |

The pipeline is orchestrated by `runs/pipeline.py::RunPipeline.execute`; `runs/manager.py`
holds the live agent session and submits the run to a worker.

---

## 3. Every source it pulls from

Sources live in `data/source_registry.yaml`. Each declares a `connector_type` (which of
the five base classes it uses), the discovery fields it provides, a join key, and a
capability doc (the text embedded for RAG retrieval). **"Built"** = a working connector
ships; un-built entries are registry placeholders the RAG planner can still reason about
but the orchestrator skips.

### 3a. Structured spine — identity, age, status (the resolution backbone)

| Source | Type | Join | Built | What it provides |
|---|---|---|---|---|
| **ABN Lookup API** (`abn_lookup_api`) | api | abn | ✅ | Live ABR REST. Name search by state, postcode discovery, ABN detail: legal/trading name, entity type, registration date (age), state, postcode, status. **The name→ABN resolution bridge.** |
| **ASIC company dataset** (`asic_company_dataset`) | bulk | acn | ✅ | ~4.4M-row ASIC register in DuckDB. ACN, status, registration/deregistration dates, company type, listed flag. Carries the ABN at 100% coverage → it **is** the ACN→ABN bridge. |
| **ABN Bulk Extract** (`abn_bulk_extract`) | bulk | abn | ✅ | ABR bulk file — the spine for **sole traders, partnerships, trusts** with no ASIC company row. Names, entity type, status, dates, address (state/postcode). No ANZSIC. Used as a resolver fallback, never swept. |
| **ASX Listed list** (`asx_listed_list`) | bulk | name | ✅ | Roster of publicly listed companies (CSV). Matched by normalized name to fire the `listed_entity` EXCLUDE. |

> Note: neither the ABN API nor the public ABN bulk extract carries ANZSIC/industry
> codes — **sector signal comes from the text/category sources below**, not the spine.

### 3b. Discovery — category-bearing text sources (find candidates by what they do)

| Source | Type | Actor / transport | Built | What it provides |
|---|---|---|---|---|
| **Google Maps** (`google_maps`) | scrape | Apify `compass/crawler-google-places` | ✅ | Business category, services text, website, reviews, location by postcode tile. Primary B2B-services discovery. |
| **Yellow Pages** (`yellow_pages`) | scrape | Apify `abotapi/yellow-pages-au-scraper` | ✅ | Directory listings by service category + contacts → sector keywords. |
| **Website fetch** (`website_fetch`) | scrape | Apify `apify/rag-web-browser` | ✅ | Homepage/about/services text → sector keyword hits, exclude hits, B2B/B2C model, moat signals. Runs in **enrichment**, not the discovery sweep. |
| **IndustryNet** (`industrynet`) | scrape | — | ✗ | Industrial/manufacturing directory (registry placeholder). |
| **Retail POS directory** (`retail_pos_directory`) | scrape | — | ✗ | Retail/hospitality storefronts (placeholder). |

### 3c. Discovery — award registers (curated, pre-vetted SMBs)

These are **AgentConnectors**: fetch public finalist pages, pull name + state structurally,
classify the business category with one LLM call, and tag `moat_signals.award_finalist`.

| Source | Type | Built | What it provides |
|---|---|---|---|
| **Trades Champion** (`trades_champion`) | agent | ✅ | Australian Trades Small Business Champion finalists/winners — **plumbing, electrical, air-conditioning/HVAC, building, construction**. The high-value off-market entry point for trade buy-boxes (e.g. the HVAC demo). |
| **Telstra Best of Business** (`telstra_awards`) | agent | ✅ | Telstra awards finalists across all sectors, by state; LLM-classified category + a tier-1 finalist quality signal. |

### 3d. Enrichment — moat & financial signals (join to resolved records by ABN)

| Source | Type | Join | Built | What it provides |
|---|---|---|---|---|
| **AusTender** (`austender`) | api | abn | ✅ | Government contracts (OCDS). Supplier ABN → contract value, count, agencies → recurring-gov-revenue moat. (No per-ABN endpoint; scans a date window + filters client-side, cached.) |
| **IPGOD** (`ipgod`) | bulk | abn | ✅ | IP Government Open Data. Patents, trademarks, designs, PBR by applicant ABN → IP moat signal. |
| **ABS CABEE** (`abs_cabee`) | bulk | anzsic | ✗ | Business counts by ANZSIC/region/turnover → market fragmentation (placeholder). |
| **IBISWorld** (`ibisworld`) | api | anzsic | ✗ | Industry revenue/EBITDA-margin benchmarks (paid, placeholder). |
| **Crunchbase** (`crunchbase`) | api | name | ✗ | PE/VC backing + investors (paid, placeholder). |

### 3e. Gated — expensive / ToS-restricted (run shortlist-only, on the top-N)

| Source | Type | Gate | Built | What it provides |
|---|---|---|---|---|
| **Inven** (`inven`) | mcp | shortlist_only | ✅ | Paid company-intelligence MCP. The **only** source that fills `pe_vc_backed` (the otherwise-inert PE/VC EXCLUDE), plus institutional ownership and a **direct** revenue estimate that beats the proxy. |
| **LinkedIn headcount** (`linkedin_headcount`) | scrape | shortlist_only | ✅ (disabled) | Employee counts from company profiles. Ships **disabled** — ToS-gated + Legal kill-switched. Feeds the proxy estimator when enabled. |

### Connector architecture

One `SourceConnector` Protocol (`fetch(params) → [RawRecord]`, `normalize(raw) →
CompanyRecord`), five base classes — a concrete connector inherits exactly one:

| Base class | Transport |
|---|---|
| `BulkConnector` | Download once → DuckDB → local SQL |
| `APIConnector` | Rate-limited HTTP + TTL cache + JSONP unwrap |
| `ScrapeConnector` | Apify actor + cache |
| `AgentConnector` | Fetch page + LLM-extract |
| `MCPConnector` | MCP tool calls |

`connectors/loader.py::load_connector(connector_ref)` dynamically instantiates from the
fully-qualified class name in the registry — the rest of the engine only calls this seam.

---

## 4. Entity resolution (the off-market bridge)

A scraped record has a **name + postcode but no ABN**. Resolution anchors it to the spine:

1. Strip company suffixes (`Pty Ltd`, `Limited`, …), name-match via **ABN Lookup**.
2. Re-rank candidates: **`0.60·name_similarity + 0.25·postcode_match + 0.15·state_match`**.
3. **Accept at RC ≥ 0.85**; keep the **0.60–0.85** band flagged `abn_match_uncertain`;
   below 0.60 → `unresolved_abn`.
4. Merge the spine: ASIC gives ACN + register name + dates (register name overrides the
   scraped display name). Sole traders/trusts with no ASIC row fall back to the **ABR
   bulk extract** for their spine merge.

Every merge appends a `Provenance(field, source, confidence)` receipt.

---

## 5. How the ranking works

`rank/rank.py::rank_pool` runs five steps. The statistical model is **locked** (an
AST-based unit test fails if the formula changes); the LLM judge is the unlocked,
qualitative layer.

### Step 1 — Screen (`rank/screen.py`): EXCLUDE → GATE → PROXY_GATE

Only **known** violations fail a record; unknown gate fields **pass but get an
`unverified:*` flag** (which the scorer then penalizes). Sequence:

- **EXCLUDE** (hard): `listed_entity` is True · `pe_vc_backed` is True · sector
  exclude-keyword hit.
- **GATE**: country ≠ Australia · `years_operating < min_years` (unknown age → flag, pass).
- **PROXY_GATE**: EBITDA estimate outside a tolerant band `[0.5·min, 1.5·max]` — and only
  when an estimate exists (the estimate is low-confidence, so the band is wide).
- **Soft**: a suspected holding/investment shell is flagged (demoted later), never failed.

### Step 2 — Statistical fit (`rank/score.py`), 0–100

```
s_sector = 0.5·s_sem + 0.3·s_kw + 0.2·s_code
fit      = 0.50·s_sector + 0.25·s_state + 0.25·s_model
adjusted = fit · (0.7 + 0.3·mean_confidence)          # confidence dampener
S_stat   = adjusted · (0.85 ^ unverified_gate_count) · 100
```

- **`s_sem`** — embedding cosine between the record's sector text and the buy-box query.
- **`s_kw`** — keyword density, or overlap of keyword hits with the buy-box keywords.
- **`s_code`** — 1.0 if the ANZSIC 4-digit prefixes intersect, else 0.
- **`s_state`** — 1.0 if the record's state is in the target set (or no geo constraint), else 0.
- **`s_model`** — 1.0 exact B2B/B2C match, 0.5 for MIXED, else 0.
- **Confidence dampener** — scales fit by how well-sourced the record is.
- **Unverified penalty** — ×0.85 per unverified gate field (honest demotion, not a fail).

**Deliberately excluded:** no `s_ai` / `s_frag` / `s_size` / `s_age` terms, and no
proxy-flag penalty. **Moat signals and awards do _not_ score here** — they inform the
judge and the analyst card only.

### Step 3 — LLM judge (`rank/judge.py`), 0–1

The top `judge_k` records by `S_stat` (default 25 in a run) go to Claude (`judge_model`,
Haiku by default). The judge reads the **whole record** — including the moat / gov-contract
/ award context that carries no statistical weight — and returns a calibrated `fit`, a
one-line `rationale`, and standout phrases. It's instructed to demote holding/investment
vehicles and sub-scale companies. Unparseable output → `judge_unavailable` (fit defaults
to 0, flagged so analysts don't trust a silent zero).

### Step 4 — Blend

```
S_final = 0.55·(S_stat / 100) + 0.45·judge_fit
```

**Standout chips** shown in the UI are derived **deterministically** from the record
(gov-contract value, agency count, accreditation, IP, award-finalist, recurring-revenue
hint) — *not* from the judge's free text, which can hallucinate figures. The judge's
qualitative read lives only in the `rationale`.

### Step 5 — Diversity guard

Cap **3 companies per postcode** in the top-`k` so the shortlist doesn't collapse onto one
area; backfill from the overflow if the cap leaves it short.

### Post-rank — Shortlist gate (`runs/shortlist_gate.py`)

Runs on the ranked **top-N only**, and **never re-scores** — it just enriches and
re-documents:

1. **LinkedIn headcount** — only if that source is enabled (it isn't by default).
2. **Inven** (if configured) — fills `pe_vc_backed`, institutional ownership, and a direct
   revenue estimate. Runs *before* the proxy so its higher-confidence revenue survives.
3. **ProxyEstimator** (always) — `revenue_est = employees × rev_per_employee(ANZSIC)`;
   `ebitda_est = revenue_est × ebitda_margin(ANZSIC)` from an ATO-style benchmark table,
   confidence capped ≤ 0.4. No headcount → an honest `unverified:ebitda_aud:no_employee_count`
   flag. This sets the PROXY_GATE band; it does **not** feed the score.

Then each company's **`deferred_assessment`** (open diligence questions) is rebuilt —
e.g. *"verify ownership — PE/VC backing not checked,"* *"confirm ABN match (RC=0.78),"*
*"verify EBITDA / financials."*

---

## 6. The honesty model

The engine never fabricates. Anything it can't verify becomes a flag with a reason, and
that flows through to both the score (the ×0.85 unverified penalty) and the analyst's
diligence checklist:

- `unverified:*` — a gate field a source couldn't fill (age, sector, EBITDA, …).
- `abn_match_uncertain` — resolution landed in the 0.60–0.85 band.
- `pe_vc_backed = None` — screened as an EXCLUDE but never verified (no source filled it),
  so it's surfaced as an open question rather than passing silently.

---

## 7. The demo-prompt cache

`runs/demo_cache.py` + `scripts/build_demo_cache.py`. The full pipeline spends Apify +
Anthropic credits and takes minutes, so canned demo prompts replay a captured run instead:

- **Match** on the buy-box prompt text (deterministic, unlike the LLM-resolved ruleset).
  `"founder owned HVAC companies; 1-5M ebitda in all of sydney area"` and close variants
  (anything with *hvac* + *sydney*/*nsw*) → key `hvac_sydney`.
- **Build** once with `python scripts/build_demo_cache.py` — runs the real pipeline for
  the canonical ruleset and dumps `source_plan` + `coverage` + `shortlist` to
  `data/demo_cache/hvac_sydney.json`.
- **Replay** — when a run's prompt matches, `RunPipeline._replay` steps through **every
  stage** (so the UI trace still animates, with the real counts) but serves the shortlist
  from the file in seconds. Toggle with `demo_cache_enabled` (default on).

> Status: the replay + matching machinery and the build script are in place; the
> `hvac_sydney.json` fixture still needs to be generated by one real run
> (`python scripts/build_demo_cache.py`) once Apify credits are available.

---

## 8. LLM & config summary

| Role | Setting | Default |
|---|---|---|
| Buy-Box agent (tool loop) | `AGENT_MODEL` | `claude-opus-4-8` |
| Signal extraction (website → JSON) | `ENRICH_MODEL` | `claude-haiku-4-5` |
| LLM judge (record → fit) | `JUDGE_MODEL` | `claude-haiku-4-5` |
| Embeddings (RAG + `s_sem`) | `EMBED_PROVIDER` | `hash` (offline, deterministic) |
| Provider | `LLM_PROVIDER` | `anthropic` (Ollama fallback) |

Run-size knobs (`config.py`): `run_plan_k` (sources planned, 8), `run_max_places` (scrape
cap per state tile, 25), `run_judge_k` (records judged, 25), `run_top_k` (shortlist size,
10), `shortlist_gate_n` (gated top-N, 10).

---

*Generated as a reference for the Origo sourcing engine. Source of truth is the code under
`src/sourcing/`; see `CLAUDE.md` and `README.md` for setup and commands.*
