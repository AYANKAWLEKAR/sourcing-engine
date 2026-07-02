# Origo Off-Market Sourcing Engine — Step 1

Foundation for the Origo sourcing engine: **database schema + data models**, the
**Buy-Box Agent** (a multi-turn conversation that builds a validated `FilterRuleset`),
and **RAG source retrieval** (rank the most relevant sources from a seeded Source
Registry by vector similarity + field-coverage filtering).

This step proves the three flows run end-to-end against a local stack and ships with
a full test suite. It uses **Ollama** for all LLM work (the Buy-Box agent's tool-use
loop and, optionally, embeddings) — no cloud API keys required.

It also ships the **connector foundation**: one `SourceConnector` Protocol, five
base classes (`BulkConnector`, `APIConnector`, `ScrapeConnector`, `AgentConnector`,
`MCPConnector`), and two fully-working concrete connectors — the **ASIC company
spine** (4.4M rows in DuckDB, the ACN→ABN bridge) and the live **ABN Lookup API**
(the entity-resolution bridge). See `sourcing-engine-step1-build-plan.md`.

---

## What's here

```
src/sourcing/
  config.py              # pydantic-settings (.env)
  db.py                  # SQLAlchemy engine / session
  models/                # Pydantic contracts: FilterRule(set), CompanyRecord, Source*, Run
  tables/                # SQLAlchemy ORM tables (mirror the models)
  ruleset/
    derive.py            # discovery_action derivation (the §1.1 matrix) — single source of truth
    loader.py            # Origo CSV -> FilterRuleset (parses logic, drops professional_licence_required)
  agent/
    buybox_agent.py      # bounded multi-turn tool-use loop
    tools.py             # tool schemas + RulesetEditor handlers
    resolvers.py         # resolve_sector / resolve_geography (seed maps + LLM fallback)
  rag/
    embeddings.py        # EmbeddingProvider: HashingEmbeddingProvider (offline) | OllamaEmbeddingProvider
    vector_store.py      # VectorStore: InMemoryVectorStore (unit) | PgVectorStore (pgvector)
    registry_seed.py     # load Source Registry from YAML
    retriever.py         # SourceRetriever: vector sim + field coverage + invariants
  llm.py                 # LLMClient: OllamaLLMClient | ScriptedLLMClient (mock)
  connectors/
    protocol.py          # SourceConnector Protocol (the contract)
    base_bulk.py         # BulkConnector — download once -> DuckDB -> local SQL
    base_api.py          # APIConnector — rate-limited + cached + JSONP unwrap
    base_scrape.py       # ScrapeConnector — Apify actor + cache (lazy import)
    base_agent.py        # AgentConnector — fetch page + LLM-extract
    base_mcp.py          # MCPConnector — MCP tool calls (stub until wired)
    cache.py             # Cache: InMemoryTTLCache (default) | RedisCache (optional)
    loader.py            # load_connector(connector_ref) — dynamic instantiation
    asic_bulk.py         # ASICBulkConnector  (bulk spine: ACN/ABN/status/age)
    abn/lookup.py        # ABNLookupAPIConnector (live JSONP: detail + name match)
    ingest.py            # upsert CompanyRecords -> Postgres companies table
data/
  origo_filter_spec.csv  # the Origo ruleset (operationalised from the spec §10)
  source_registry.yaml   # seeded sources + capability docs + connector_refs
  bulk.duckdb            # local ASIC spine cache (gitignored; built by `asic-load`)
cli.py                   # buybox / sources / ruleset / fetch-abn / asic-*
migrations/              # Alembic (0001_init: all tables + pgvector)
tests/unit/              # deterministic, offline
tests/integration/       # marked; require Postgres + Ollama
```

### Key interfaces (so later steps slot in without refactors)
- **`LLMClient`** — `chat(model, system, messages, tools)`. `OllamaLLMClient` for live;
  `ScriptedLLMClient` injected in tests.
- **`EmbeddingProvider`** — `embed(texts) -> vectors`. `HashingEmbeddingProvider` is a
  deterministic, dependency-free default (real lexical-overlap cosine, used by the unit
  suite and the offline CLI); `OllamaEmbeddingProvider` swaps in for live embeddings.
- **`VectorStore`** — `upsert_many` / `query`. `InMemoryVectorStore` (unit) and
  `PgVectorStore` (pgvector) behind one contract.

---

## Prerequisites

- **Python 3.12+**
- **Docker** (for Postgres + pgvector)
- **[Ollama](https://ollama.com)** with a **tool-calling** model. This repo defaults to
  `gpt-oss:20b`. Any tool-capable model works (`llama3.1`, `qwen2.5`, …) — set `AGENT_MODEL`.

---

## Setup

```bash
cd sourcing-engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # adjust if needed (see notes below)
```

### Start the stack

```bash
docker compose up -d          # Postgres 16 + pgvector
alembic upgrade head          # create schema (tables + `vector` extension + vector column)

# Ollama (in a separate shell, if not already running):
ollama serve
ollama pull gpt-oss:20b       # the agent model (tool calling)
```

> **Ports:** the bundled `.env` binds Postgres on host port **5433** (`PG_PORT`/`DATABASE_URL`)
> to avoid clashing with other local Postgres instances. Change both if you prefer 5432.

> **Ollama embeddings (optional):** the default `EMBED_PROVIDER=hash` needs no model and
> keeps tests offline & deterministic. To use live embeddings, `ollama pull nomic-embed-text`,
> then set `EMBED_PROVIDER=ollama`, `EMBED_DIM=768`, and re-run `alembic upgrade head`
> against a fresh DB (the pgvector column dimension must match the provider).

---

## Running the tests

```bash
# Unit suite — offline, deterministic, no services needed:
pytest -m "not integration"

# With coverage on the core logic (target >= 90%):
pytest -m "not integration" \
  --cov=sourcing.ruleset --cov=sourcing.agent.tools --cov=sourcing.rag.retriever \
  --cov-report=term-missing

# Connectivity suite — requires Postgres (docker) + Ollama running:
pytest -m integration
```

The unit suite covers: the discovery-action matrix (every `tier × scrapeable × proxyable`),
the CSV loader (rule count, dropped field, logic parsing, derived actions, weights), the
Buy-Box tools and the full agent loop (scripted mock LLM), and RAG retrieval (indexing,
relevance, both invariants, disabled-source exclusion, field coverage, cost ceiling).

The integration suite checks: `SELECT 1`, the `vector` extension + a cosine query,
`alembic upgrade head` produces all expected tables/columns, Ollama chat returns content,
the embedding provider returns a vector of `EMBED_DIM`, and a full pgvector index→retrieve
round-trip with the invariants holding live.

Integration tests **skip cleanly** (not fail) when a service is down.

---

## CLI demo

```bash
# 1) Inspect the base ruleset loaded from the Origo CSV:
python cli.py ruleset

# 2) Buy-Box Agent — live multi-turn conversation (needs Ollama):
python cli.py buybox
#   ...or seed the first turn non-interactively:
python cli.py buybox --buy-box "Founder-owned B2B testing & certification firms in QLD, $1-15M EBITDA"
# Emits a confirmed, schema-valid FilterRuleset (sector -> ANZSIC + keywords,
# geography -> postcodes) within the question cap, or flags NEEDS REVIEW at the cap.

# 3) RAG source retrieval — ranked, explainable Source Plan (offline by default):
python cli.py sources "B2B testing & certification services in QLD"
# Prints ranked sources with rationale + which discovery fields each contributes,
# and confirms the invariants (spine present, >=1 text source present).
```

---

## Source connectors

One Protocol, five base classes, concrete connectors on top. **A concrete
connector never implements the Protocol directly** — it inherits from exactly one
base class, chosen by the registry entry's `connector_type` (`bulk` → `BulkConnector`,
`api` → `APIConnector`, etc.). The `loader.load_connector(connector_ref)` seam is
how the rest of the engine obtains a working connector.

```
SourceConnector (Protocol):  fetch(params) -> [RawRecord];  normalize(raw) -> CompanyRecord
  ├── BulkConnector    download once -> DuckDB -> local SQL        → ASICBulkConnector ✅
  ├── APIConnector     live HTTP, rate-limited + cached + JSONP    → ABNLookupAPIConnector ✅
  ├── ScrapeConnector  managed Apify actor + cache                 → GoogleMaps/YellowPages/Website/LinkedIn ✅
  ├── AgentConnector   fetch page + LLM-extract                    → TelstraAwardsConnector ✅
  └── MCPConnector     MCP tool calls                              (Inven — stub)
```

**Connectors built and verified end-to-end** (highlights — see the registry for the full set):

- **ASIC company spine** (`asic_company_dataset`, `BulkConnector`). Loads the ~4.4M-row
  ASIC extract into DuckDB in ~7s. `all_varchar` preserves ACN/ABN leading zeros; the
  extract carries **both ACN and ABN (100% coverage)**, so it *is* the ACN→ABN bridge.
  Methods: `lookup_acn` / `lookup_abn` (indexed point lookups), `fetch({entity_types,
  min_years, status, state, limit})` (deduped candidate slice), `normalize`.
- **ABN Lookup API** (`abn_lookup_api`, `APIConnector`). Live JSONP — the resolution
  bridge. `fetch({abn})` → full detail; `fetch({name, state})` → up to 20 scored
  name-match candidates the entity resolver re-ranks. Rate-limited to ~4 rps, cached 7 days.
- **Telstra Best of Business awards** (`telstra_awards`, `AgentConnector`). The first
  agent connector — a *discovery* source. Sweeps public finalist pages (rag-web-browser),
  pulls **name + state structurally** (verbatim, conf 0.9) and classifies each finalist's
  **business category with one plain-text qwen call** (conf 0.5). Yields a curated pool of
  award-finalist SMBs with `moat_signals.award_finalist=True` — resolved to ABN downstream
  like a Maps record; the ranker's judge already weighs the signal. Live: 39 finalists from
  one 2025 category page. (Plain text, not JSON mode — grammar-constrained JSON decoding of a
  ~40-item list is pathologically slow on CPU-only qwen.)

The `APIConnector` cache defaults to an **in-process TTL cache** (offline, no Redis);
set `REDIS_URL` to use Redis. Scrape/agent connectors need `APIFY_API_TOKEN` to run live
but are unit-tested offline with an injected fake client.

### Connector CLI

```bash
# ASIC spine — load (first run downloads into data/bulk.duckdb), then query offline:
python cli.py asic-load                    # load/verify; prints row count + ABN coverage
python cli.py asic-lookup 000000019        # point-lookup by ACN or ABN
python cli.py asic-fetch --types APTY --min-years 20 --limit 20   # candidate slice
python cli.py asic-fetch --types APTY --min-years 20 --save       # + upsert to Postgres

# ABN Lookup API (needs ABN_LOOKUP_GUID in .env):
python cli.py fetch-abn 51824753556        # full detail for an ABN
python cli.py fetch-abn "Acme Plumbing" --state QLD   # scored name matches
```

> **Credentials:** `ABN_LOOKUP_GUID` (free, from abr.business.gov.au/Tools/WebServices)
> and `ASIC_CSV_PATH` (local path to the ASIC extract) go in `.env`. The scrape/agent
> connectors will additionally need `APIFY_API_TOKEN`.

### Connector tests

```bash
# Offline (mocked transport / Apify client / DuckDB fixture):
pytest tests/unit/test_base_classes.py tests/unit/test_bulk_connectors.py \
       tests/unit/test_api_connectors.py tests/unit/test_registry.py \
       --cov=sourcing.connectors --cov-report=term-missing      # 92% coverage

# Live (needs ABN_LOOKUP_GUID for ABN; ASIC_CSV_PATH for ASIC):
pytest -m integration tests/integration/test_abn_lookup.py tests/integration/test_asic_bulk.py
```

The base-layer tests cover Protocol conformance for all five base classes, the rate
limiter (mocked clock), the API and scrape caches, JSONP unwrap, and the loader.
`test_registry.py` asserts every *built* connector resolves to the base class implied
by its `connector_type` — the one test that catches a connector on the wrong base class.

---

## Enrichment & ranking (turning resolved records into a ranked shortlist)

After discovery + resolution, two stages produce the shortlist. **All LLM work runs on a
local Ollama/qwen model — no cloud API.**

**Part A — Enrichment** (`src/sourcing/enrichment/`)
- `austender.py` (`AusTenderConnector`, `APIConnector`) — government-contract moat signal,
  joined on supplier ABN. (The live OCDS API has no per-ABN endpoint, so it scans a bounded
  date window and filters by supplier ABN client-side; the window response is cached.)
- `signal_extractor.py` — website text → `business_model`, `keyword_hits`, ANZSIC, moat
  signals, via **qwen in JSON mode**.
- `proxy_estimator.py` (+ `data/ato_benchmarks.csv`) — rough revenue/EBITDA for the
  PROXY_GATE flag only (confidence ≤ 0.4); does not score.
- `enrichment_node.py` — wires AusTender + website-fetch + signal extractor over the pool.

**Part B — Screen & Rank** (`src/sourcing/rank/`)
- `screen.py` — EXCLUDE → GATE → PROXY_GATE.
- `score.py` — the locked model `fit = 0.50·s_sector + 0.25·s_state + 0.25·s_model`, a
  confidence dampener, and an unverified-gate penalty. **No** `s_ai`/`s_frag`/`s_size`/`s_age`,
  no proxy penalty (a unit test guards this via AST).
- `judge.py` — qwen reads the full record (incl. moat/award context that carries no
  statistical weight) and returns a calibrated fit.
- `rank.py` — `S_final = 0.55·(S_stat/100) + 0.45·judge_fit`, then a postcode diversity guard
  → `RankedCompany` top-N.

### Ollama / qwen on Docker

```bash
docker compose up -d ollama                 # adds an Ollama service (compose)
docker exec <ollama> ollama pull qwen2.5:3b # 3b = fast on CPU; qwen2.5:7b for quality
# config: enrich_model / judge_model (default qwen2.5:3b)
```

### End-to-end demo (no ABNs in → ranked shortlist out)

```bash
python scripts/rank_demo.py
# Maps discovery → resolve → AusTender + website→qwen signals → screen → score → qwen judge → top-N
```

Tests: `pytest tests/unit/test_enrichment.py tests/unit/test_ranking.py tests/unit/test_austender_connector.py`
(offline — fake LLM + fake connectors). The scoring math (dampener + unverified penalty) is
asserted exactly on fixtures.

---

## How Step 1 maps to the plan's definition of success

| Success criterion (plan §9) | Where |
|---|---|
| `docker compose up` + `alembic upgrade head` provisions schema incl. pgvector | `docker-compose.yml`, `migrations/versions/0001_init.py` |
| Unit suite green, ≥90% coverage on `ruleset/`, `agent/tools.py`, `rag/retriever.py` | `tests/unit/` |
| Integration suite green (DB, pgvector, LLM, embeddings connect) | `tests/integration/` |
| Loader yields schema-valid ruleset, correct discovery actions, no `professional_licence_required` | `ruleset/loader.py`, `tests/unit/test_loader.py` |
| Buy-Box agent emits a confirmed ruleset (sector + geography resolved) within the cap | `agent/`, `cli.py buybox` |
| RAG returns an explainable ranked list satisfying both invariants, excluding disabled, respecting coverage | `rag/retriever.py`, `cli.py sources` |
| `EmbeddingProvider` / `VectorStore` / `LLMClient` injectable with fake + prod impls | `rag/embeddings.py`, `rag/vector_store.py`, `llm.py` |

---

## Handoff to the next connectors

The connector hierarchy, the ASIC spine, the live ABN Lookup bridge, the scrape layer, the
entity resolver, the enrichment + ranking pipeline, and the first agent connector (Telstra
awards) are all in place and tested. What remains slots onto the existing base classes:

- **Agent** (`AgentConnector`): the other award registers — Trades Champion, Local Business
  Awards — reuse `AwardRegisterConnector` (set `program`/`base_url_template`/`category_slugs`).
- **MCP** (`MCPConnector`): Inven (stub → wired when the MCP server is connected).
- **Part C**: persist a `Run` in Postgres + a FastAPI surface (`POST /runs`, `GET /runs/{id}`).
- **Part D**: the analyst UI (buy-box chat → run progress → shortlist → company detail).
