# Audit Fixes — Sourcing Engine

Engineering audit performed 2026-07-01. 18 issues found and resolved.
All fixes are covered by offline unit tests in `tests/unit/test_audit_fixes.py`.

---

## Fix 1 — Missing BuyBox → connector params translation layer

**File:** `src/sourcing/orchestrator.py` (new file)  
**Root cause:** `rank_demo.py` manually hardcoded a `DISCOVERY` param dict that bore no relationship to the `BuyBox`. Any change to the buy-box would silently fail to propagate to the scrape layer.  
**Change:** Created `SourcingOrchestrator` and `params_for_connector()` as the canonical translation layer. `params_for_connector(source_id, buybox)` derives `fetch()` parameters from the `BuyBox` for every supported source type. `SourcingOrchestrator.fetch_all(plan, buybox)` iterates the `SourcePlan`, derives buy-box params per source, calls each connector, and returns an aggregated `list[CompanyRecord]`.

---

## Fix 2 — Geographic tiling not implemented for scrape connectors

**File:** `src/sourcing/orchestrator.py` (new file)  
**Root cause:** Scrape connectors (Google Maps, Yellow Pages) accept a single location string per call. Multi-state buy-boxes (e.g. `states=["QLD","NSW"]`) would only ever cover one state.  
**Change:** `params_for_connector()` returns **one dict per state** for sources in `_TILED_SOURCES` (`google_maps`, `yellow_pages`, `industrynet`, `retail_pos_directory`). Each tile contains the state's canonical location string (e.g. `"Queensland Australia"`) so the actor covers the full geographic scope. `SourcingOrchestrator.fetch_all()` iterates all returned tiles, calling `connector.fetch()` once per tile.

---

## Fix 3 — ASIC spine incorrectly filtered by state

**File:** `src/sourcing/orchestrator.py` (new file)  
**Root cause:** The ASIC `previous_state` column records the state of *incorporation*, not the business's *current operating state*. A Queensland HVAC company incorporated in NSW would be silently excluded from any QLD-scoped ASIC query.  
**Change:** `params_for_connector()` **never** passes `state` to sources in `_SPINE_SOURCES` (`asic_company_dataset`, `abn_bulk_extract`). Geographic screening belongs in the `s_state` scorer after ABN resolution merges the operating address from the ABN spine.

---

## Fix 4 — Sector exclude keywords silently dropped

**File:** `src/sourcing/rank/buybox.py`  
**Root cause:** `FilterRule.parse_logic()` for `filter_type="keyword"` puts anti-fit keywords under the `"include"` key in its returned dict. `BuyBox.from_ruleset()` only read `"values"` and `"exclude"` from the `sector_exclude_match` rule, so the `"include"` list was never loaded into `sector_exclude_keywords`.  
**Change:** Added `excl.get("include")` alongside the existing `"values"` and `"exclude"` reads so all three key variants from the parsed logic are captured.

```python
# Before
excludes = list(sector.get("exclude") or [])
excludes += list(logic("sector_exclude_match").get("values") or [])
excludes += list(logic("sector_exclude_match").get("exclude") or [])

# After
excl = logic("sector_exclude_match")
excludes = list(sector.get("exclude") or [])
excludes += list(excl.get("include") or [])   # ← was missing
excludes += list(excl.get("values") or [])
excludes += list(excl.get("exclude") or [])
```

---

## Fix 5 — Thread-unsafe entity resolver shared state

**File:** `src/sourcing/enrichment/entity_resolution.py`  
**File:** `tests/unit/test_entity_resolution.py` (updated)  
**Root cause:** `EntityResolver` stored the winning candidate in `self._last_match`. Concurrent `enrich()` calls from `ThreadPoolExecutor` would overwrite each other's `_last_match`, causing state (operating state, postcode) from one record's resolution to bleed into another's.  
**Change:** Removed `self._last_match`. `resolve()` now returns a 3-tuple `(abn, confidence, candidate_dict)`. `enrich()` reads the returned tuple directly. Concurrent calls are fully isolated with no shared mutation. Existing unit tests updated from `abn, rc = r.resolve(...)` to `abn, rc, cand = r.resolve(...)`.

---

## Fix 6 — Cache singleton not actually singleton

**File:** `src/sourcing/connectors/cache.py`  
**Root cause:** `get_default_cache()` created a new `InMemoryTTLCache()` on every call. Every connector that called it got a separate in-memory cache, so a prior Apify response cached by `GoogleMapsConnector` was not visible to `YellowPagesConnector` — doubling credit spend on identical lookups.  
**Change:** Added a module-level `_default_cache: Cache | None = None` and made `get_default_cache()` implement the create-once pattern. Added `reset_default_cache()` as a test-isolation helper (tests that need isolation already pass `cache=InMemoryTTLCache()` explicitly — they are not affected).

---

## Fix 7 — DuckDB TOCTOU race in BulkConnector

**File:** `src/sourcing/connectors/base_bulk.py`  
**Root cause:** Two threads calling `ensure_loaded()` concurrently could both read `table_exists() == False`, then both attempt to `DROP TABLE + CREATE TABLE`, corrupting the DuckDB file.  
**Change:** Added a per-`db_path` `threading.Lock` (held in module-level `_db_locks` dict guarded by `_db_locks_meta`). `ensure_loaded()` acquires the lock before the TOCTOU check so at most one thread ever calls `load()` for a given db path.

---

## Fix 8 — Signal extractor text truncated at 4 000 chars

**File:** `src/sourcing/enrichment/signal_extractor.py`  
**Root cause:** Website text was truncated to 4 000 characters before being sent to the LLM, covering only ~600 words — often cutting off before the first product/service description.  
**Change:** Added `_MAX_TEXT_CHARS = 8000` module-level constant and changed the truncation slice from `text[:4000]` to `text[:_MAX_TEXT_CHARS]`. This covers ~1 200 words, enough to reach the core service description on most SME websites, while still fitting within context limits for `qwen2.5:3b`.

---

## Fix 9 — No deduplication before or after resolution

**File:** `src/sourcing/connectors/dedup.py` (new file)  
**Root cause:** The same business appearing in Google Maps and Yellow Pages results led to two ABN Lookup API calls and two enrichment rounds for the same entity.  
**Change:** Created two deduplication helpers:

- `deduplicate_by_abn(records)` — after resolution, collapses records with the same ABN, keeping the one with more provenance entries. Unresolved records (no ABN) pass through unchanged.
- `deduplicate_pre_resolution(records)` — before resolution, collapses records with the same `(legal_name, postcode)` pair, keeping the one with richer contacts (website > phone > empty). Saves ABN Lookup API calls.

---

## Fix 10 — EnrichmentNode bypassed normalize() contract

**File:** `src/sourcing/enrichment/enrichment_node.py`  
**Root cause:** The website enrichment path called `website.fetch()` and then read raw dict keys (`"markdown"`, `"text"`) directly, bypassing `connector.normalize()`. This meant the `WebsiteFetchConnector`'s domain logic (encoding normalisation, markdown cleaning, deferred-assessment fallback) was skipped.  
**Change:** Changed `enrich_one()` to call `self.website.normalize(first)` on the first returned raw record and read `normalized.website_text_raw or normalized.deferred_assessment.get("website_text_raw")`. All website text now passes through the connector's normalisation contract.

---

## Fix 11 — LLM judge failure mode invisible in downstream data

**File:** `src/sourcing/rank/judge.py`  
**File:** `src/sourcing/models/ranking.py`  
**File:** `src/sourcing/rank/rank.py`  
**Root cause:** When the local Ollama judge is unavailable or returns malformed JSON, `LLMJudge.judge()` returned a silent `JudgeResult(fit=0.0, ...)` that was indistinguishable from a genuine low-fit result. Downstream analysts couldn't filter out records where the judge simply failed.  
**Change:**  
- Added `unavailable: bool = False` to `JudgeResult`.  
- The fallback path sets `JudgeResult(fit=0.0, rationale="judge unavailable", unavailable=True)`.  
- Added `judge_unavailable: bool = False` to `RankedCompany`.  
- `rank_pool()` propagates `jr.unavailable` into the `RankedCompany` it creates.

---

## Fix 12 — AusTender window too narrow; not configurable

**File:** `src/sourcing/connectors/austender.py`  
**File:** `src/sourcing/config.py`  
**Root cause:** The default `window_days=180` missed contract history going back two years, understating the government-contracts moat signal. `max_pages=3` cut off large contractors. Neither value was configurable without code changes.  
**Change:**  
- Changed `default_window_days` class attribute to `730` (2 years).  
- Changed `max_pages` to `10`.  
- Added `austender_window_days: int = 730` to `Settings` (reads `AUSTENDER_WINDOW_DAYS` env var).  
- Added `__init__` to `AusTenderConnector` that reads `get_settings().austender_window_days`, overriding the class default at runtime.

---

## Fix 13 — Award register connector has no degradation detection

**File:** `src/sourcing/connectors/awards.py`  
**Root cause:** If the award-program website changes its HTML structure, the regex parser silently returns an empty list, producing zero records with no warning — indistinguishable from a legitimately empty award year.  
**Change:** After regex extraction, compare the number of H4 headers found on the page against the number of extracted blocks. If `h4_count > 1` and `len(blocks) < h4_count * 0.5`, emit a `warnings.warn()` with `stacklevel=2` and a message containing `"award_page_extraction_degraded"`. This lets monitoring surfaces catch structural HTML changes early.

---

## Fix 14 — Entity ID collisions for scrape records without stable IDs

**File:** `src/sourcing/connectors/google_maps.py`  
**File:** `src/sourcing/connectors/yellow_pages.py`  
**Root cause:** When the Apify actor did not return a `placeId`/`cid`/`fid` (Google Maps) or `id`/`url` (Yellow Pages), the entity_id fell back to the bare `title` or `name`. Two different businesses with the same trading name (e.g. two "Smith Plumbing" franchises in different postcodes) would share the same entity_id, causing one to silently overwrite the other in downstream maps.  
**Change:** When no stable ID is present, compute a deterministic SHA-1 hash of `f"{name}-{postcode}-{state}"` and use `"hash:" + hexdigest[:12]` as the ID component. Two businesses with the same name in different locations now get different entity_ids. The hash is stable across runs (same inputs → same hash) so deduplication still works.

---

## Fix 15 — EnrichmentNode ThreadPoolExecutor not exposed

**File:** `src/sourcing/enrichment/enrichment_node.py`  
**Root cause:** `enrich_pool()` ran enrichment sequentially, even though AusTender HTTP calls and website fetches are independent per record and IO-bound.  
**Change:** Added `max_workers: int | None = None` parameter to `enrich_pool()`. When set, a `ThreadPoolExecutor(max_workers=max_workers)` is used; default `None` keeps the sequential path for callers that don't need concurrency.

---

## Fix 16 — Rate limiter not thread-safe

**File:** `src/sourcing/connectors/base_api.py`  
**Root cause:** `_RateLimiter.acquire()` read and updated `self._last_call` without a lock. Two threads could both read `elapsed > min_interval`, both skip the sleep, and both fire requests simultaneously, bursting past the configured rate.  
**Change:** Added `self._lock = threading.Lock()` in `_RateLimiter.__init__` and wrapped the entire acquire body in `with self._lock:` so only one thread at a time can check and update `_last_call`.

---

## Fix 17 — No connector instance cache; stateful connectors re-created on every call

**File:** `src/sourcing/connectors/connector_registry.py` (new file)  
**Root cause:** Every call to `load_connector()` produced a new instance. For `BulkConnector` (ASIC), this opened a new DuckDB connection and loaded the full 4.4 M-row table again. For `APIConnector`, it reset the in-memory rate-limiter state.  
**Change:** Created `ConnectorRegistry`: a thread-safe `connector_ref → instance` cache with a process-level singleton (`ConnectorRegistry.get()`). `get_or_create(ref)` instantiates exactly once per ref under a `threading.Lock`. `ConnectorRegistry.reset()` replaces the singleton (test helper). `SourcingOrchestrator` and `EnrichmentNode` use the singleton by default.

---

## Fix 18 — No checkpoint callback for incremental persistence

**File:** `src/sourcing/enrichment/enrichment_node.py`  
**Root cause:** `enrich_pool()` returned all records in one batch. A crash mid-run lost all enrichment work already completed. There was no way to persist each record immediately after enrichment for resumable runs.  
**Change:** Added `checkpoint: Callable[[CompanyRecord], None] | None = None` parameter to `enrich_pool()`. After each record is enriched (regardless of `max_workers`), `checkpoint(record)` is called if provided. Callers can use this to write to a database, append to a JSONL file, or update a progress store without modifying `EnrichmentNode` itself.

---

## Files changed

| File | Status | Fixes |
|------|--------|-------|
| `src/sourcing/orchestrator.py` | New | 1, 2, 3 |
| `src/sourcing/rank/buybox.py` | Modified | 4 |
| `src/sourcing/enrichment/entity_resolution.py` | Modified | 5 |
| `src/sourcing/connectors/cache.py` | Modified | 6 |
| `src/sourcing/connectors/base_bulk.py` | Modified | 7 |
| `src/sourcing/enrichment/signal_extractor.py` | Modified | 8 |
| `src/sourcing/connectors/dedup.py` | New | 9 |
| `src/sourcing/enrichment/enrichment_node.py` | Modified | 10, 15, 18 |
| `src/sourcing/rank/judge.py` | Modified | 11 |
| `src/sourcing/models/ranking.py` | Modified | 11 |
| `src/sourcing/rank/rank.py` | Modified | 11 |
| `src/sourcing/connectors/austender.py` | Modified | 12 |
| `src/sourcing/config.py` | Modified | 12 |
| `src/sourcing/connectors/awards.py` | Modified | 13 |
| `src/sourcing/connectors/google_maps.py` | Modified | 14 |
| `src/sourcing/connectors/yellow_pages.py` | Modified | 14 |
| `src/sourcing/connectors/base_api.py` | Modified | 16 |
| `src/sourcing/connectors/connector_registry.py` | New | 17 |
| `src/sourcing/models/source.py` | Modified | gate field |
| `tests/unit/test_entity_resolution.py` | Modified | 5 |
| `tests/unit/test_audit_fixes.py` | New | all 18 |
