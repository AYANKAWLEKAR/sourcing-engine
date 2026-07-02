"""Application settings, loaded from environment / .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

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

    # LLM (Ollama — runs locally / on Docker; no cloud API used)
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_timeout: float = 900.0  # generous — CPU-only qwen is slow on big list extractions
    agent_model: str = "gpt-oss:20b"
    # Enrichment + ranking models (qwen by default — strong local JSON output).
    # 3b is the default for CPU-only Docker (fast); set to qwen2.5:7b for quality
    # if you have GPU/Metal or patience.
    enrich_model: str = "qwen2.5:3b"   # signal extractor (website text -> JSON signals)
    judge_model: str = "qwen2.5:3b"    # LLM judge (full record -> calibrated fit)

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

    # Apify (scrape connectors: Google Maps, Yellow Pages, Website, LinkedIn)
    apify_api_token: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
