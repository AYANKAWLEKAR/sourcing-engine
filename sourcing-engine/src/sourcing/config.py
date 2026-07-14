"""Application settings, loaded from environment / .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (src/sourcing/config.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://sourcing:sourcing@localhost:5432/sourcing"

    # LLM provider: "anthropic" (Claude Messages API, default) or "ollama"
    # (local/self-hosted fallback).
    llm_provider: str = "anthropic"

    # Anthropic (Claude) — the default provider.
    anthropic_api_key: str = ""
    llm_timeout: float = 120.0   # per-request HTTP timeout (Claude calls are seconds, not minutes)
    llm_max_tokens: int = 4096   # ample for tool calls + JSON extraction + judge output
    # Model per role. Default to the most capable Opus; enrich/judge are high-volume,
    # so set ENRICH_MODEL/JUDGE_MODEL=claude-haiku-4-5 in .env to trade quality for cost.
    agent_model: str = "claude-opus-4-8"   # buy-box agent (tool-use loop)
    enrich_model: str = "claude-opus-4-8"  # signal extractor (website text -> JSON signals)
    judge_model: str = "claude-opus-4-8"   # LLM judge (full record -> calibrated fit)

    # Ollama fallback (used only when llm_provider="ollama").
    ollama_host: str = "http://localhost:11434"
    ollama_timeout: float = 900.0  # generous — CPU-only local models are slow on big extractions

    # Embeddings
    embed_provider: str = "hash"  # hash | ollama
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 384

    # Agent loop
    max_clarifying_questions: int = 6

    # ABN Lookup API (https://abr.business.gov.au/Tools/WebServices)
    abn_lookup_guid: str = ""

    # Bulk-source files. Production resolves these via CKAN (data.gov.au); for
    # local dev/test, point at a downloaded copy.
    asic_csv_path: str = ""

    # ABN bulk extract (data.gov.au ABR): two zips (public_split_1_10.zip,
    # public_split_11_20.zip) under abn_bulk_dir (default data/abn_bulk).
    abn_bulk_dir: str = ""
    abn_bulk_download: bool = False  # allow live CKAN download when zips absent (~1.7GB)
    abn_bulk_enabled: bool = False   # wire the ABN-bulk fallback into EntityResolver

    # IPGOD applicant CSVs, comma-separated; ip_type inferred from each filename.
    ipgod_csv_paths: str = ""

    # ASX listed-companies CSV; empty -> newest data/ASX_Listed_Companies_*.csv.
    asx_csv_path: str = ""

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> Settings:
        if self.asic_csv_path and not Path(self.asic_csv_path).is_absolute():
            self.asic_csv_path = str(REPO_ROOT / self.asic_csv_path)
        if self.abn_bulk_dir and not Path(self.abn_bulk_dir).is_absolute():
            self.abn_bulk_dir = str(REPO_ROOT / self.abn_bulk_dir)
        if self.asx_csv_path and not Path(self.asx_csv_path).is_absolute():
            self.asx_csv_path = str(REPO_ROOT / self.asx_csv_path)
        if self.cache_path and not Path(self.cache_path).is_absolute():
            self.cache_path = str(REPO_ROOT / self.cache_path)
        if self.ipgod_csv_paths:
            resolved = []
            for p in self.ipgod_csv_paths.split(","):
                p = p.strip()
                if p and not Path(p).is_absolute():
                    p = str(REPO_ROOT / p)
                if p:
                    resolved.append(p)
            self.ipgod_csv_paths = ",".join(resolved)
        return self

    # AusTender enrichment window (Fix 12: raised to 2 years; 180 days missed
    # suppliers with contracts older than 6 months).
    austender_window_days: int = 730

    # Apify (scrape connectors: Google Maps, Yellow Pages, Website, LinkedIn)
    apify_api_token: str = ""

    # Inven (paid MCP source: pe_vc_backed, institutional ownership, direct revenue).
    # Both must be set for the shortlist gate to wire Inven; otherwise pe_vc stays an
    # honest "unchecked" in the diligence checklist.
    inven_mcp_url: str = ""
    inven_mcp_token: str = ""
    # Connector cache backend: "memory" (process-local, default/tests),
    # "sqlite" (persistent across runs — Apify/website results survive restarts,
    # so repeat runs don't re-bill), or "redis" (when REDIS_URL is set).
    cache_backend: str = "memory"
    # Persistent-cache file (sqlite backend + the ABN CompanyRecord cache).
    cache_path: str = "data/cache.sqlite"
    # TTL for cached enriched CompanyRecords keyed by ABN (default 14 days).
    record_cache_ttl_seconds: int = 14 * 24 * 3600

    # Cap on how many resolved sector keywords are passed as scrape search terms.
    # A broad buy-box can expand to ~20 keywords; Google Maps crawls
    # maxCrawledPlacesPerSearch PER term, so uncapped terms mean a huge, slow
    # scrape (a 20-term run took ~17 min for ~2.7k places). Keep the most
    # relevant few.
    scrape_max_search_terms: int = 6
    # Hard wall-clock ceiling per Apify actor run (the actor self-aborts past it),
    # so a slow/broad scrape can't block a run indefinitely.
    scrape_actor_timeout_secs: int = 240

    # Run orchestration (Part C). With Claude the judge is fast (~seconds/call),
    # so the pool is sized for coverage; Apify spend, not LLM latency, is the cap.
    run_workers: int = 1          # concurrent pipeline executions
    run_plan_k: int = 8           # sources in the retrieved SourcePlan (RAG path only)
    run_use_all_sources: bool = True  # bypass RAG selection — plan every enabled source
    run_max_places: int = 25      # scrape cap per state tile (Apify cost bound)
    run_enrich_workers: int = 4   # concurrent enrichment threads within a run
    run_top_k: int = 30           # shortlist size
    run_judge_k: int = 40         # records sent to the LLM judge (must exceed run_top_k)
    shortlist_gate_n: int = 30    # top-N passed through the shortlist gate

    # Demo-prompt cache: replay a captured run for canned prompts (see runs/demo_cache).
    demo_cache_enabled: bool = True
    demo_cache_replay_seconds: float = 0.9   # per-stage dwell so the trace animates


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
