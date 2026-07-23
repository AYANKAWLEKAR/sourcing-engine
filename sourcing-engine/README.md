# Origo Off-Market Sourcing Engine (Step 1)

The **Origo Sourcing Engine** is an advanced, AI-driven pipeline designed to automate off-market company sourcing and identification for Private Equity. Starting from a natural language **Buy-Box prompt**, the engine drives a multi-turn conversational agent to establish precise filter rules, maps out capability criteria to target specific databases via semantic search (RAG), crawls and resolves target entities, extracts public signals (websites, government contract moats), and scores candidates using statistical models combined with an LLM judge.

It is built as a robust Python stack that runs locally, integrating **Postgres + pgvector**, **DuckDB**, and local/cloud LLMs.

---

## 🏗️ Architecture & Pipeline Flow

The engine operates as a sequential, stage-gated pipeline:

```mermaid
graph TD
    A[Buy-Box NL Prompt] --> B[Buy-Box Agent]
    B -->|FilterRuleset| C[RAG Source Retriever]
    C -->|Source Plan| D[Acquisition & Crawling]
    D -->|Candidate Pool| E[Entity Resolution]
    E -->|Resolved ABNs| F[Enrichment Node]
    F -->|Signal Extraction & Moats| G[Screen, Score & Rank]
    G -->|Ranked Shortlist| H[Streamlit Analyst UI]
```

### 1. **Buy-Box Agent** (`src/sourcing/agent/`)
A conversational loop (Ollama or Claude) that prompts the user to clarify geographic, sector, and sizing bounds. It outputs a validated, schema-compliant `FilterRuleset` mapping ANZSIC codes and postal/state boundaries.

### 2. **RAG Source Retriever** (`src/sourcing/rag/`)
Performs cosine similarity searches over a seeded **Source Registry** (`data/source_registry.yaml`). It enforces structural invariants (e.g., ensuring a primary database spine is matched alongside at least one text/discovery source) to generate an optimized **Source Plan**.

### 3. **Acquisition & Connectors** (`src/sourcing/connectors/`)
Implements a unified `SourceConnector` protocol with five specialized transport layers:
*   `BulkConnector`: Large-scale local datasets (e.g., the **4.4M-row ASIC company dataset** loaded into DuckDB).
*   `APIConnector`: Live HTTP interfaces (e.g., ABN Lookup, AusTender) featuring rate-limiting and local SQLite caches.
*   `ScrapeConnector`: Web crawlers (e.g., Google Maps, YellowPages, LinkedIn via Apify).
*   `AgentConnector`: Scraping + LLM-based structured information extraction (e.g., Telstra Business Award finalists).
*   `MCPConnector`: Model Context Protocol integration (e.g., Inven database connectivity).

### 4. **Entity Resolution** (`src/sourcing/enrichment/entity_resolution.py`)
Uses the live ABN Lookup API and the ASIC spine to bridge discovery matches (e.g., Google Maps/awards names) with corporate entities, applying a weighted similarity index (`0.60·name_similarity + 0.25·postcode_match + 0.15·state_match`) to map targets to unique ABNs.

### 5. **Enrichment Node** (`src/sourcing/enrichment/`)
Crawls company websites and runs local/cloud LLMs in structured JSON mode to extract business models, ANZSIC alignment, and core keywords. It also sweeps the Australian Government tender registry (**AusTender**) to extract historical contract moats.

### 6. **Screen, Score & Rank** (`src/sourcing/rank/`)
*   **Screening**: Applies strict logic gates (`EXCLUDE` / `GATE` / `PROXY_GATE`).
*   **Statistical Score**: Evaluates candidates using a locked formula: `fit = 0.50·s_sector + 0.25·s_state + 0.25·s_model`.
*   **LLM Judge**: Reviews qualitative signals (moats, awards) to provide a calibrated fit score.
*   **Diversity Guard**: Caps top-N candidates from the same postcode to prevent regional concentration.

---

## 🛠️ Step-by-Step Setup Guide

### 1. Prerequisites
Ensure you have the following installed:
*   **Python 3.12+**
*   **Docker & Docker Compose**
*   **Ollama** (for a 100% offline local setup) OR an **Anthropic API Key** (for production runs).

---

### 2. Environment Configuration
Copy the template and configure your credentials:
```bash
cd sourcing-engine
cp .env.example .env
```

Review and adjust the `.env` settings:
```ini
# Database & Core
DATABASE_URL=postgresql://sourcing:sourcing@localhost:5433/sourcing
PG_PORT=5433  # Binds on 5433 to prevent clashing with local Postgres

# LLM Providers (Choose "anthropic" or "ollama")
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-api-key-here
AGENT_MODEL=claude-3-5-sonnet-20241022

# If using local Ollama:
# LLM_PROVIDER=ollama
# OLLAMA_HOST=http://localhost:11434
# AGENT_MODEL=gpt-oss:20b  # Tool-calling model of your choice
# ENRICH_MODEL=qwen2.5:3b  # Faster model for extraction/judging

# Embeddings Configuration
EMBED_PROVIDER=hash  # Default (hashing provider, offline/deterministic)
EMBED_DIM=768        # Match your embedding dimension (e.g. 768 for nomic-embed-text)

# Source Credentials
ABN_LOOKUP_GUID=your-abn-lookup-guid-here  # Free from abr.business.gov.au
APIFY_API_TOKEN=your-apify-token-here      # For scrape/crawling connectors
ASIC_CSV_PATH=data/company_202606.csv      # Path to ASIC CSV spine
```

---

### 3. Bootstrap the Stack
Run the following commands to initialize your database, apply migrations, and build the ASIC DuckDB index:

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies (dev, ui, and connectors)
pip install -e ".[dev,ui,connectors]"

# 3. Start local database & services
docker compose up -d db

# 4. Run database migrations (creates schema, vectors, & tables)
alembic upgrade head

# 5. Load and index the ASIC Bulk Company dataset (~4.4M rows) into DuckDB
# Note: Downloads a mock subset automatically if a local CSV is not present
python cli.py asic-load
```

---

## 🏃 Running the Sourcing Engine

The project offers three pathways to execute runs:

### Pathway A: Streamlit Analyst UI (Interactive)
The Streamlit interface allows PE analysts to run the multi-turn Buy-Box builder, monitor live pipeline stages, and inspect ranked results interactively.

```bash
# Serves both the FastAPI server (:8000) and Streamlit UI (:8501)
python cli.py serve --ui
```
*   **Access**: Open `http://localhost:8000` (which redirects automatically to Streamlit at `http://localhost:8501`).

### Pathway B: CLI Synchronous Execution (Production)
Run a complete sourcing pipeline directly from the command line, bypassing the interactive UI:

```bash
# Run a full pipeline and persist results to Postgres
python cli.py run "Founder-owned B2B testing & certification firms in QLD, $1-15M EBITDA" --yes

# Run entirely in-memory (bypassing Postgres)
python cli.py run "HVAC installers in QLD" --no-db --yes
```

### Pathway C: Developer Replay Demo (Zero-Scrape Cost)
Evaluate the scoring and UI rendering without consuming live scraping credits or LLM tokens:

```bash
# Serves a pre-cached run from the demo cache
python scripts/rank_demo.py
```

---

## 🧪 Testing & Code Quality

### Running the Test Suite
The codebase is guarded by a comprehensive test suite splitting unit (deterministic & offline) and integration (requiring services) tests.

```bash
# 1. Run all unit tests (offline, no external servers required)
pytest -m "not integration"

# 2. Check test coverage on the core engine files (target >= 90%)
pytest -m "not integration" \
  --cov=sourcing.ruleset --cov=sourcing.agent.tools --cov=sourcing.rag.retriever \
  --cov-report=term-missing

# 3. Run integration tests (requires docker containers + live connections)
pytest -m integration
```

### Formatting & Linting
We enforce code hygiene using **Ruff**:
```bash
# Check code structure & compliance
ruff check src/ tests/

# Automatically apply safe fixes
ruff check --fix src/ tests/
```

---

## 🗃️ Core Data Contracts (`src/sourcing/models/`)

Developers working on new connectors or features should adhere to these core Pydantic contracts:

*   **`FilterRuleset`**: Represents the structured criteria extracted from the Buy-Box conversation (sectors, postcodes, excluded structures, revenue ranges).
*   **`CompanyRecord`**: The data envelope representing a company candidate as it traverses through the pipeline. Fields (location, age, size, moat flags) are incrementally filled.
*   **`RankedCompany`**: The final payload containing a `CompanyRecord`, statistical score (`s_stat`), LLM fit (`judge_fit`), and detailed text explanations.
