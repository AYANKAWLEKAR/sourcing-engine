# Demo Setup & Execution Guide — Sourcing Engine

This guide is designed for developers, agents, and automated runners to set up, configure, and execute the full off-market company sourcing engine pipeline. 

---

## 1. Prerequisites & Environment Setup

Before running the pipeline, ensure the stack is healthy and properly configured.

### Environment Checklist (`.env`)
Ensure the following variables are correctly populated in your `.env` file at the root:
- `DATABASE_URL=postgresql://sourcing:sourcing@localhost:5433/sourcing` (default port is `5433`)
- `LLM_PROVIDER=anthropic` (or `ollama` for a local setup)
- `ANTHROPIC_API_KEY=your_anthropic_api_key`
- `APIFY_API_TOKEN=your_apify_api_token`
- `ABN_LOOKUP_GUID=your_guid_here`
- `ASIC_CSV_PATH=data/company_202606.csv`

### Commands to Bootstrap the Stack
```bash
# 1. Start Postgres + pgvector container (port 5433)
docker compose up -d db

# 2. Apply database migrations to create schema
alembic upgrade head

# 3. Load the ASIC company dataset (~4.4M records) into DuckDB
# Note: The loader dynamically normalizes and parses the CSV headers
python cli.py asic-load
```

---

## 2. Synthetic Buy-Box Prompts (Test Scenarios)

Use these synthetic buy-box prompts to test different pipeline pathways, criteria, and filters:

### Scenario A: HVAC Installers in Queensland (Standard Scrape & Match)
> **NL Prompt:** *"Founder-owned HVAC installers or air conditioning services in Brisbane QLD, with EBITDA between $500k and $5M. Exclude venture-backed firms."*
* **Target Behavior**: Derives rules for `states=["QLD"]`, `business_model="B2B"`, `ebitda_min=500000`, `ebitda_max=5000000`, `exclude_pe_vc=True`, and keywords like `["HVAC", "air conditioning"]`.

### Scenario B: Testing & Certification Services (Long-standing Businesses)
> **NL Prompt:** *"B2B testing and certification services in Queensland, minimum 10 years operating."*
* **Target Behavior**: Sets `min_years=10`, `states=["QLD"]`, `business_model="B2B"`, and includes keywords like `["testing services", "accreditation", "calibration", "certification"]`.

### Scenario C: Commercial Refrigeration in Sydney
> **NL Prompt:** *"Commercial refrigeration contractors in Sydney, NSW. Make sure to check for government contract moats."*
* **Target Behavior**: Scrapes Sydney NSW, maps refrigeration keywords, and relies heavily on the `AusTender` contract value and `ip` count to calculate the deterministic `evidence_score`.

---

## 3. How to Run the Sourcing Engine

You can execute a sourcing run via three main paths depending on the context:

### Path A: Streamlit Analyst UI (Interactive)
Best for manual exploration and live multi-turn buy-box alignment.
```bash
# Start the FastAPI server and launch the Streamlit analyst UI in a subprocess
python cli.py serve --ui
```
* **Location**: Navigate to `http://localhost:8501`. 
* **Flow**: Input prompt → Conclude chat → Watch live progress bar → View ranked shortlist drawer.

### Path B: CLI Synchronous Execution (Production / Scripted Runs)
Best for automated pipeline triggers and developer testing.
```bash
# Runs the full pipeline from prompt to final shortlist, persisting results to DB
python cli.py run "B2B testing and certification services in QLD" --yes
```
* **Use `--no-db` flag** if you want to bypass local Postgres and run entirely in-memory:
  ```bash
  python cli.py run "HVAC installers in Brisbane" --no-db --yes
  ```

### Path C: Developer Replay Demo (Zero-Scrape Cost)
Best for styling, testing, or UI evaluations without consuming Apify credits or LLM tokens.
```bash
# Serves a pre-cached run from the demo_cache folder (replays stage transitions)
python scripts/rank_demo.py
```

---

## 4. Pipeline Stages & Expected Outputs

When a run is started, the pipeline transitions through the following stages. Below is an explanation of what happens and what the output looks like.

### Stage 1: `buybox` (Interactive Chat)
* **Action**: The Buy-Box Agent (Claude/Ollama) clarifying geography, sectors, and criteria rules.
* **Under the Hood**: Compiles the conversation history into a validated `FilterRuleset` (which gets persisted to Postgres with a prefix `rs_{run_id}`).
* **Expected CLI / Logs Output**:
  ```text
  [Buy-Box Agent]: Understood. Let's look for HVAC installers in QLD.
  Proposed geography: QLD (Queensland Australia)
  Proposed keywords: ['HVAC installer', 'air conditioning services']
  [User]: Confirm and finalize.
  Ruleset finalized: rs_run_e1b73e527d
  ```

### Stage 2: `planning` (Source Retrieval)
* **Action**: Maps the ruleset to the Source Registry.
* **Under the Hood**:
  - If `run_use_all_sources=True` is enabled in config, it automatically selects all 17 enabled sources (bypassing vector RAG query).
  - Otherwise, it performs cosine similarity checks to generate a bounded `SourcePlan` containing the structured spine and relevant text scraping candidates.
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: PLANNING ---
  Retrieving sources for thesis: 'HVAC installers in QLD'...
  Planned sources: ['google_maps', 'yellow_pages', 'asic_company_dataset', 'austender']
  ```

### Stage 3: `acquiring` (Discovery & Crawling)
* **Action**: Connectors execute external sweeps to gather raw business records.
* **Under the Hood**: 
  - Scrapers like `GoogleMapsConnector` partition the query geographically by state tiles (e.g. `Queensland Australia`).
  - Calls Apify store actors (e.g. `compass/crawler-google-places`).
  - Deduplicates candidates pre-resolution based on `(legal_name, postcode)`.
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: ACQUIRING ---
  Executing google_maps for tile QLD...
  Deduplicating raw records...
  Acquired 80 raw candidates.
  ```

### Stage 4: `resolving` (Entity Resolution)
* **Action**: Matches ABN-less crawl candidates against official registers.
* **Under the Hood**:
  - Queries ABN Lookup API with name-match rules.
  - Normalizes scores based on names and geography.
  - Merges matched rows with local ASIC bulk company table.
  - Deduplicates resolved records by ABN to remove duplicates across sources.
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: RESOLVING ---
  Resolving candidate names to ABN spine...
  Resolved 24 companies to verified ABNs.
  ```

### Stage 5: `enriching` (Moats & Site Scraping)
* **Action**: Hydrates resolved records with structural data and website content.
* **Under the Hood**:
  - Checks if the company is an ASX-listed entity or has patents/trademarks in IPGOD.
  - Scrapes the company website using Apify `apify/rag-web-browser` and runs LLM `SignalExtractor` to extract B2B/B2C business models, keyword hits, and moat signals.
  - Persists intermediate records incrementally after each record is enriched (checkpointing).
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: ENRICHING ---
  Enriching pool with AusTender + IPGOD + Website Text...
  [Checkpoint] Enriched ABN 51824753556 (Acme Air Pty Ltd) - Cache: MISS
  ```

### Stage 6: `ranking` (Fit Calculations & LLM Judge)
* **Action**: Sorts and scores candidates to build the final shortlist.
* **Under the Hood**:
  - Runs local, deterministic rule-based Screening.
  - Calculates local `statistical_fit` (sectors, geography, models).
  - Sends top `judge_k=40` candidates to the LLM Judge for a qualitative fit score and rationale.
  - Calculates the deterministic `evidence_score` ($S_{ev}$) using contract value, awards, IP count, and EBITDA accuracy.
  - Blends the final score: $S_{final} = 0.40 \cdot S_{stat} + 0.25 \cdot judge\_fit + 0.35 \cdot S_{ev}$.
  - Applies a postcode-diversity cap.
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: RANKING ---
  Calculating statistical fit...
  Running LLM fit judge on top 40 candidates...
  Blending final scores...
  Shortlist generated (top 30 companies).
  ```

### Stage 7: `complete` (Persistence)
* **Action**: The run completes and registers the final list.
* **Under the Hood**: Persists the shortlist and stage history to Postgres.
* **Expected CLI / Logs Output**:
  ```text
  --- Stage: COMPLETE ---
  Run run_e1b73e527d finalized successfully.
  ```
