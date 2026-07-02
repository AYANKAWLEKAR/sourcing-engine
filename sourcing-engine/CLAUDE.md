# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools directly.

Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/document-generate`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

## Commands

All commands run from `sourcing-engine/` with the venv active (`source .venv/bin/activate`).

```bash
# Install
pip install -e ".[dev]"                  # core + test deps
pip install -e ".[connectors]"           # adds apify-client + redis (scrape/agent connectors)

# Services
docker compose up -d                     # Postgres 16 + pgvector (port 5433)
alembic upgrade head                     # create schema (run after compose up)

# Lint
ruff check src/ tests/                   # line-length=100, rules: E F I UP B
ruff check --fix src/ tests/

# Tests — unit (offline, no services required)
pytest -m "not integration"
pytest -m "not integration" --cov=sourcing.ruleset --cov=sourcing.agent.tools --cov=sourcing.rag.retriever --cov-report=term-missing

# Tests — single file
pytest tests/unit/test_ranking.py -v

# Tests — integration (needs Postgres + Ollama running)
pytest -m integration

# CLI
python cli.py ruleset                    # inspect loaded FilterRuleset from CSV
python cli.py buybox                     # multi-turn Buy-Box Agent (needs Ollama)
python cli.py sources "B2B testing QLD"  # ranked Source Plan (offline by default)
python cli.py asic-load                  # load ASIC CSV -> data/bulk.duckdb
python cli.py asic-lookup 000000019      # point-lookup by ACN or ABN
python cli.py fetch-abn 51824753556      # live ABN Lookup API call

# Demos
python scripts/rank_demo.py              # end-to-end: discovery → resolve → enrich → rank
```

## Architecture

The engine is a pipeline: **Buy-Box Agent → Source Retrieval → Discovery → Entity Resolution → Enrichment → Screen/Score/Rank**.

### Key data contracts (`src/sourcing/models/`)

- `FilterRuleset` — the structured buy-box: rules with `tier` (DISQUALIFIER/HARD/SOFT/MANUAL), `discovery_action` (EXCLUDE/GATE/PROXY_GATE/SCORE/DEFER_*), `scrapeable`, `proxyable`, `weight`. The `derive.py` matrix is the single source of truth for which action a rule gets.
- `CompanyRecord` — the unit that flows through the pipeline. Nested sub-models: `Location`, `Sector`, `Age`, `Size`, `Ownership`, `MoatSignals`, `AwardSignal`, `Screen`. Resolution sets `abn`; enrichment fills `moat_signals`/`sector`; ranking writes `screen`.
- `SourceRegistryEntry` / `SourcePlanItem` — source metadata from `data/source_registry.yaml`.
- `RankedCompany` — wraps `CompanyRecord` with `s_stat`, `s_final`, `judge_fit`, `judge_rationale`, `standout_signals`, `deferred_assessment`.

### Connector hierarchy (`src/sourcing/connectors/`)

One `SourceConnector` Protocol (two methods: `fetch(params) → [RawRecord]`, `normalize(raw) → CompanyRecord`). Five base classes — a connector inherits exactly one:

| Base class | Transport | Built connectors |
|---|---|---|
| `BulkConnector` | Download once → DuckDB → local SQL | `ASICBulkConnector` |
| `APIConnector` | Rate-limited HTTP + TTL cache + JSONP | `ABNLookupAPIConnector`, `AusTenderConnector` |
| `ScrapeConnector` | Apify actor + cache | `GoogleMapsConnector`, `YellowPagesConnector`, `WebsiteConnector`, `LinkedInConnector` |
| `AgentConnector` | Fetch page + LLM-extract | `TelstraAwardsConnector` |
| `MCPConnector` | MCP tool calls | stub (Inven) |

`loader.load_connector(connector_ref)` dynamically imports and instantiates a connector from its fully-qualified class name. The rest of the engine only calls this seam — never imports concrete connectors directly. `data/source_registry.yaml` maps `source_id` → `connector_ref` and `connector_type`.

### Key modules

- **`ruleset/derive.py`** — `derive_discovery_action(tier, scrapeable, proxyable)` — the `tier × scrapeable × proxyable → DiscoveryAction` matrix. **Do not inline this logic elsewhere.**
- **`ruleset/loader.py`** — parses `data/origo_filter_spec.csv` into a `FilterRuleset`. Drops `professional_licence_required`. Parses logic strings like `in:[NSW, QLD]`.
- **`agent/buybox_agent.py`** — bounded multi-turn tool-use loop. Calls `resolve_sector`, `resolve_geography`, `update_ruleset`, and `finalize_ruleset` tools. Capped at `max_clarifying_questions` (default 6).
- **`rag/retriever.py`** — `SourceRetriever`: vector cosine similarity over capability docs + field coverage filter + cost ceiling. Two invariants always enforced: spine source present, ≥1 text source present.
- **`enrichment/entity_resolution.py`** — `EntityResolver.resolve(name, postcode, state)`: name → ABN via ABN Lookup API, re-ranked `0.60·name_sim + 0.25·postcode + 0.15·state`, merges ASIC spine fields. Accept threshold 0.85; keep with `abn_match_uncertain` flag at 0.60–0.85.
- **`enrichment/enrichment_node.py`** — orchestrates AusTender enrichment + website fetch + signal extractor over the candidate pool.
- **`enrichment/signal_extractor.py`** — website text → `business_model`, `keyword_hits`, ANZSIC, moat signals via qwen in JSON mode.
- **`rank/rank.py`** — `rank_pool(pool, buybox)`: screen → statistical score (top 50) → LLM judge (blended `S_final = 0.55·(S_stat/100) + 0.45·judge_fit`) → postcode diversity cap.
- **`rank/score.py`** — locked scoring model: `fit = 0.50·s_sector + 0.25·s_state + 0.25·s_model`. No `s_ai`/`s_frag`/`s_size`/`s_age`. An AST-based unit test guards the formula.
- **`llm.py`** — `LLMClient` interface (`OllamaLLMClient` live; `ScriptedLLMClient` for tests).
- **`rag/embeddings.py`** — `EmbeddingProvider`: `HashingEmbeddingProvider` (offline/deterministic) or `OllamaEmbeddingProvider`.
- **`rag/vector_store.py`** — `VectorStore`: `InMemoryVectorStore` (unit tests) or `PgVectorStore` (pgvector).
- **`connectors/cache.py`** — `InMemoryTTLCache` (default) or `RedisCache` (set `REDIS_URL`).

### Settings (`src/sourcing/config.py`)

Pydantic-settings, loaded from `.env`. Key vars: `DATABASE_URL` (port 5433 by default), `AGENT_MODEL` (default `gpt-oss:20b`), `ENRICH_MODEL`/`JUDGE_MODEL` (default `qwen2.5:3b`), `EMBED_PROVIDER` (`hash`|`ollama`), `ABN_LOOKUP_GUID`, `ASIC_CSV_PATH`, `APIFY_API_TOKEN`.

### Test conventions

- Unit tests (`tests/unit/`) are fully offline. LLM is injected as `ScriptedLLMClient`. Apify is faked. DuckDB uses an in-memory fixture.
- Integration tests (`tests/integration/`) are marked `@pytest.mark.integration` and skip cleanly when services are down.
- `test_registry.py` asserts every built connector resolves to the base class implied by its `connector_type` — catches a connector on the wrong base.
- The scoring formula in `rank/score.py` is guarded by an AST test that fails if the formula is changed without updating the test.

### Data files

- `data/origo_filter_spec.csv` — the Origo filter ruleset (§10 of the spec). Source of truth for `FilterRuleset` defaults.
- `data/source_registry.yaml` — source metadata + connector refs + capability docs used by the RAG retriever.
- `data/bulk.duckdb` — local ASIC spine cache (gitignored; built by `python cli.py asic-load`).
- `data/ato_benchmarks.csv` — ATO revenue benchmarks used by `proxy_estimator.py` for PROXY_GATE estimates.
