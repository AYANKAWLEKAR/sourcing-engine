"""AgentConnector — base class for fetch-a-page + LLM-extract sources (plan §2.5).

Concrete agent connectors (Telstra / Trades / Local award registers) inherit from
this, implement ``fetch(params)`` to build the program URL, and ``normalize(raw)``
to map the extracted fields to a ``CompanyRecord`` + signal.

``_fetch_and_extract(url, extract_prompt)`` fetches the page via the
``apify/rag-web-browser`` actor (returning markdown), then asks a small/fast model
to pull structured fields out of that markdown.

Both the Apify client and the LLM client are lazy/injectable so the hierarchy and
offline unit tests work without Apify or a live model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base_scrape import ScrapeConnector
from .protocol import RawRecord

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

_RAG_BROWSER_ACTOR = "apify/rag-web-browser"


class AgentConnector(ScrapeConnector):
    """Base for page-fetch + LLM-extract connectors.

    Reuses ``ScrapeConnector``'s Apify client/cache to fetch the page, then layers
    an LLM extraction step. Subclasses set ``source_id`` and implement
    ``fetch``/``normalize``; ``actor_id`` is fixed to the rag-web-browser actor.
    """

    actor_id: str = _RAG_BROWSER_ACTOR
    cache_ttl_seconds: int = 14 * 24 * 3600

    def __init__(self, *, cache: Any = None, client: Any = None, llm: Any = None) -> None:
        super().__init__(cache=cache, client=client)
        self._injected_llm = llm  # tests pass a fake extractor here

    def _fetch_page_markdown(self, url: str) -> str:
        """Fetch a single page as markdown via the rag-web-browser actor."""
        items = self._run_actor({"query": url, "maxResults": 1, "outputFormats": ["markdown"]})
        if not items:
            return ""
        first = items[0]
        return first.get("markdown") or first.get("text") or ""

    def _fetch_and_extract(self, url: str, extract_prompt: str) -> dict:
        """Fetch ``url`` and LLM-extract structured fields per ``extract_prompt``.

        ``llm`` (injected) is any callable ``(prompt, content) -> dict``. When not
        injected, falls back to the configured Ollama client.
        """
        markdown = self._fetch_page_markdown(url)
        extractor = self._injected_llm or self._default_extractor()
        return extractor(extract_prompt, markdown)

    def _default_extractor(self) -> Any:
        """Build the fallback extractor: a JSON LLM call on the enrich model.

        Returns a callable ``(prompt, content) -> dict`` (the extractor contract).
        Uses ``complete_json`` with the ``enrich_model`` — NOT the agent model —
        and forces JSON output.
        """
        from ..config import get_settings
        from ..llm import complete_json, get_llm_client

        settings = get_settings()
        client = get_llm_client(settings)

        def _extract(prompt: str, content: str) -> dict:
            return complete_json(
                client,
                settings.enrich_model,
                "Extract the requested fields and respond with ONLY a JSON object.",
                f"{prompt}\n\n---\n{content}",
            )

        return _extract

    # ------------------------------------------------------------------
    # Contract — subclasses implement these
    # ------------------------------------------------------------------

    def fetch(self, params: dict) -> list[RawRecord]:  # pragma: no cover - abstract
        raise NotImplementedError

    def normalize(self, raw: RawRecord) -> CompanyRecord:  # pragma: no cover - abstract
        raise NotImplementedError
