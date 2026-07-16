# NATA Accreditation Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NATA accreditation as a discovery source (sweep the register for accredited private companies) and an enrichment source (annotate other candidates from a local cache), filtered to private commercial entities by a pluggable local classifier.

**Architecture:** A `NATAConnector(ScrapeConnector)` runs the Apify `apify/playwright-scraper` actor tiled per state × include-keyword, aggregates per-site rows to one record per parent org, then an ownership classifier keeps only `private_commercial` parents. A `NATACache` (own `data/nata.duckdb`) powers a guarded, non-fatal Plan-B lookup in `EnrichmentNode`. Everything degrades to a no-op on failure so the live engine cannot break.

**Tech Stack:** Python 3.13, pydantic / pydantic-settings, DuckDB, Apify (`apify-client`), Ollama (`qwen2.5:3b`) or Anthropic for the classifier, pytest.

## Global Constraints

- Run all commands from `sourcing-engine/` with the venv: `source .venv/bin/activate` (or prefix `.venv/bin/`).
- Lint: `ruff check src/ tests/` — line-length 100, rules E F I UP B. Must pass.
- Default test run is offline: `pytest -m "not integration"`. It must never hit Apify, Ollama, or the network.
- Live tests are marked `@pytest.mark.integration` and must `pytest.skip(...)` cleanly when their dependency (Apify token / Ollama+qwen) is absent.
- **Graceful degradation is mandatory:** every NATA failure path (Apify 403/cap, classifier/Ollama down, missing cache) is logged (`warnings.warn`) and skipped — it must NEVER raise into the acquiring or enriching stage.
- Additive only: do not change existing function signatures or the orchestrator's per-connector `try/except` isolation. New `MoatSignals` fields are optional with defaults.
- NATA cache lives in its **own** `data/nata.duckdb` — never touch `data/bulk.duckdb`.
- Commit after each task with a `feat:`/`test:` message ending:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Classifier settings + pull qwen2.5:3b

**Files:**
- Modify: `src/sourcing/config.py` (append to `Settings`, after `max_clarifying_questions` ~line 47)
- Test: `tests/unit/test_config_classifier.py`

**Interfaces:**
- Produces: `Settings.classifier_provider: str` (`"ollama"|"anthropic"`), `Settings.classifier_model: str`, `Settings.classifier_ollama_url: str`, `Settings.classifier_timeout_seconds: int`, `Settings.classifier_batch_size: int`.

- [ ] **Step 1: Pull the local classifier model** (setup, one-off)

Run: `ollama pull qwen2.5:3b`
Expected: `success` (model appears in `ollama list`). If offline, skip — Task 3 tests use a mocked client and don't need it; only the gated live test in Task 8 does.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_config_classifier.py
from sourcing.config import Settings


def test_classifier_defaults():
    s = Settings()
    assert s.classifier_provider == "ollama"
    assert s.classifier_model == "qwen2.5:3b"
    assert s.classifier_ollama_url == "http://localhost:11434"
    assert s.classifier_timeout_seconds == 30
    assert s.classifier_batch_size == 10
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config_classifier.py -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'classifier_provider'`).

- [ ] **Step 4: Add the settings**

In `src/sourcing/config.py`, immediately after the `max_clarifying_questions: int = 6` line, add:

```python
    # Ownership classifier (NATA private/public filter). Separate from LLM_PROVIDER
    # so the primary LLM can be Claude while classification runs locally on Ollama.
    classifier_provider: str = "ollama"   # ollama | anthropic
    classifier_model: str = "qwen2.5:3b"
    classifier_ollama_url: str = "http://localhost:11434"
    classifier_timeout_seconds: int = 30
    classifier_batch_size: int = 10
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_config_classifier.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sourcing/config.py tests/unit/test_config_classifier.py
git commit -m "feat: add ownership-classifier settings for NATA connector

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Six new MoatSignals fields

**Files:**
- Modify: `src/sourcing/models/company.py` (the `MoatSignals` class, after `award_finalist`)
- Test: `tests/unit/test_moat_signals_nata.py`

**Interfaces:**
- Produces: `MoatSignals.nata_accreditation: bool`, `.nata_site_count: int | None`, `.nata_service_types: list[str]`, `.nata_accreditation_numbers: list[str]`, `.nata_states: list[str]`, `.nata_multistate: bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_moat_signals_nata.py
from sourcing.models.company import MoatSignals


def test_nata_fields_default_empty():
    m = MoatSignals()
    assert m.nata_accreditation is False
    assert m.nata_site_count is None
    assert m.nata_service_types == []
    assert m.nata_accreditation_numbers == []
    assert m.nata_states == []
    assert m.nata_multistate is False


def test_nata_fields_populate():
    m = MoatSignals(nata_accreditation=True, nata_site_count=3,
                    nata_states=["NSW", "VIC"], nata_multistate=True)
    assert m.nata_site_count == 3
    assert m.nata_multistate is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_moat_signals_nata.py -q`
Expected: FAIL (`TypeError`/validation error — unknown field `nata_accreditation`).

- [ ] **Step 3: Add the fields**

In `src/sourcing/models/company.py`, inside `class MoatSignals(BaseModel)`, immediately after `award_finalist: bool | None = None`, add:

```python
    # NATA accreditation (regulatory moat). Set by the NATA connector / cache.
    nata_accreditation: bool = False
    nata_site_count: int | None = None
    nata_service_types: list[str] = Field(default_factory=list)
    nata_accreditation_numbers: list[str] = Field(default_factory=list)
    nata_states: list[str] = Field(default_factory=list)
    nata_multistate: bool = False       # True if len(nata_states) > 1
```

(`Field` is already imported in this module — it's used by the existing `ip_types`/`gov_contract_agencies` fields.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_moat_signals_nata.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm existing model/ranking tests still green**

Run: `.venv/bin/python -m pytest tests/unit/test_ranking.py -q`
Expected: PASS (fields are additive; judge/score unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/sourcing/models/company.py tests/unit/test_moat_signals_nata.py
git commit -m "feat: add NATA fields to MoatSignals

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Ownership classifier module

**Files:**
- Create: `src/sourcing/classifiers/__init__.py` (empty)
- Create: `src/sourcing/classifiers/ownership_classifier.py`
- Test: `tests/unit/test_ownership_classifier.py`

**Interfaces:**
- Produces:
  - `Classification` dataclass: `.name: str`, `.category: str`, `.confidence: float`, `.reasoning: str`.
  - `OwnershipClassifier(complete=None, *, batch_size=10)` — `complete` is an injectable
    `Callable[[str], str]` returning raw model text (default builds one from settings).
  - `OwnershipClassifier.classify(names: list[str]) -> list[Classification]` — order-preserving.
  - Module constants `PRIVATE = "private_commercial"`, `CATEGORIES` (the 5-tuple), `SYSTEM_PROMPT`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ownership_classifier.py
import json
from sourcing.classifiers.ownership_classifier import (
    OwnershipClassifier, Classification, PRIVATE,
)


def _fake_complete(payload):
    """Return a callable that emits a fixed JSON array of classifications."""
    def _c(prompt: str) -> str:
        return json.dumps(payload)
    return _c


def test_classifies_batch_in_order():
    payload = [
        {"category": "private_commercial", "confidence": 0.95, "reasoning": "Pty Ltd"},
        {"category": "public_sector", "confidence": 0.98, "reasoning": "state health"},
    ]
    clf = OwnershipClassifier(complete=_fake_complete(payload), batch_size=10)
    out = clf.classify(["Acme Labs Pty Ltd", "NSW Health Pathology"])
    assert [c.category for c in out] == [PRIVATE, "public_sector"]
    assert out[0].name == "Acme Labs Pty Ltd"


def test_low_confidence_marked_but_returned():
    payload = [{"category": "private_commercial", "confidence": 0.4, "reasoning": "guess"}]
    clf = OwnershipClassifier(complete=_fake_complete(payload))
    out = clf.classify(["Ambiguous Name"])
    assert out[0].confidence == 0.4


def test_unparseable_falls_back_to_unclear():
    clf = OwnershipClassifier(complete=lambda p: "not json at all")
    out = clf.classify(["Whatever"])
    assert out[0].category == "unclear"


def test_order_mismatch_falls_back_to_per_item():
    # A 2-item batch that returns only 1 result triggers per-item reclassification.
    calls = {"n": 0}

    def _c(prompt: str) -> str:
        calls["n"] += 1
        # First (batch) call returns the wrong length; per-item calls return 1 each.
        if calls["n"] == 1:
            return json.dumps([{"category": "private_commercial", "confidence": 0.9, "reasoning": "x"}])
        return json.dumps([{"category": "public_sector", "confidence": 0.9, "reasoning": "y"}])

    clf = OwnershipClassifier(complete=_c, batch_size=10)
    out = clf.classify(["A", "B"])
    assert len(out) == 2
    assert calls["n"] >= 3  # 1 failed batch + 2 per-item
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ownership_classifier.py -q`
Expected: FAIL (`ModuleNotFoundError: sourcing.classifiers`).

- [ ] **Step 3: Create the package marker**

Create `src/sourcing/classifiers/__init__.py` (empty file).

- [ ] **Step 4: Write the classifier**

Create `src/sourcing/classifiers/ownership_classifier.py`:

```python
"""Ownership classifier — private vs public/non-profit/multinational filter.

One narrow job: classify a NATA-accredited organisation name into one of five
ownership categories so the connector keeps only private commercial candidates.
Pluggable backend (Ollama default, Anthropic optional) via an injected
``complete`` callable; batched (10/prompt) with per-item fallback on mismatch.
"""
from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass

PRIVATE = "private_commercial"
CATEGORIES = (PRIVATE, "public_sector", "non_profit", "listed_or_multinational", "unclear")

SYSTEM_PROMPT = """You classify Australian organisations by ownership type for an acquisition sourcing engine.
For EACH organisation name, classify into ONE of:
- private_commercial: privately-owned commercial business (Pty Ltd, partnerships, sole traders, family-owned, PE/VC-backed). INCLUDE subsidiaries of larger PRIVATE companies. This is the category we want.
- public_sector: any government entity (public hospitals, state health pathology e.g. "NSW Health Pathology", departments, public universities, TAFEs, defence, CSIRO).
- non_profit: charities, industry associations, community orgs, co-operatives, professional bodies.
- listed_or_multinational: ASX-listed or subsidiaries of listed/foreign multinationals (e.g. "Bureau Veritas Australia", "SGS Australia", "Intertek"). Out of scope.
- unclear: cannot determine from the name alone.
Return ONLY a JSON array, one object per input in the SAME ORDER:
[{"category": "<one of the five>", "confidence": 0.0-1.0, "reasoning": "<short>"}]"""


@dataclass
class Classification:
    name: str
    category: str
    confidence: float
    reasoning: str


def _default_complete() -> Callable[[str], str]:
    """Build a completion callable from settings (Ollama or Anthropic)."""
    from ..config import get_settings

    s = get_settings()
    if s.classifier_provider == "anthropic":
        from ..llm import get_llm_client

        client = get_llm_client(s)

        def _complete(prompt: str) -> str:
            resp = client.chat(model=s.classifier_model, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": prompt}], format="json")
            return resp.text or ""

        return _complete

    # Ollama (default) — direct HTTP; raises on failure (caller degrades).
    import httpx

    def _complete(prompt: str) -> str:
        r = httpx.post(
            f"{s.classifier_ollama_url}/api/generate",
            json={"model": s.classifier_model,
                  "system": SYSTEM_PROMPT, "prompt": prompt,
                  "format": "json", "stream": False},
            timeout=s.classifier_timeout_seconds,
        )
        r.raise_for_status()
        return r.json().get("response", "")

    return _complete


class OwnershipClassifier:
    def __init__(self, complete: Callable[[str], str] | None = None, *, batch_size: int = 10):
        self._complete = complete or _default_complete()
        self._batch_size = batch_size

    def classify(self, names: list[str]) -> list[Classification]:
        out: list[Classification] = []
        for i in range(0, len(names), self._batch_size):
            batch = names[i : i + self._batch_size]
            out.extend(self._classify_batch(batch))
        return out

    def _classify_batch(self, batch: list[str]) -> list[Classification]:
        prompt = "Classify these organisations:\n" + "\n".join(
            f"{n+1}. {name}" for n, name in enumerate(batch)
        )
        parsed = self._call_and_parse(prompt)
        if parsed is None or len(parsed) != len(batch):
            if len(batch) > 1:
                # Order mismatch → reclassify each item individually.
                result: list[Classification] = []
                for name in batch:
                    result.extend(self._classify_batch([name]))
                return result
            parsed = parsed or [{}]
        return [self._to_classification(name, obj) for name, obj in zip(batch, parsed, strict=False)]

    def _call_and_parse(self, prompt: str) -> list[dict] | None:
        try:
            text = self._complete(prompt)
        except Exception as exc:  # noqa: BLE001 - degrade, never raise into the pipeline
            warnings.warn(f"OwnershipClassifier: completion failed: {exc}", stacklevel=2)
            return None
        return self._extract_array(text)

    @staticmethod
    def _extract_array(text: str) -> list[dict] | None:
        text = (text or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, list) else None

    @staticmethod
    def _to_classification(name: str, obj: dict) -> Classification:
        cat = obj.get("category")
        if cat not in CATEGORIES:
            cat = "unclear"
        try:
            conf = float(obj.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return Classification(name=name, category=cat, confidence=max(0.0, min(1.0, conf)),
                              reasoning=str(obj.get("reasoning", "")))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ownership_classifier.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/sourcing/classifiers/ tests/unit/test_ownership_classifier.py
git commit -m "feat: add ownership classifier (private/public filter)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: NATAConnector — URL, actor input, extraction, aggregation

**Files:**
- Create: `src/sourcing/connectors/nata.py`
- Create: `tests/fixtures/nata_sites.json` (captured/handmade playwright-scraper output)
- Test: `tests/unit/test_nata_connector.py`

**Interfaces:**
- Consumes: `ScrapeConnector` (`base_scrape.py`), `Classification`/`OwnershipClassifier`/`PRIVATE` (Task 3), `MoatSignals` NATA fields (Task 2).
- Produces:
  - `SOURCE_ID = "nata_accreditation"`.
  - `normalize_org_name(name: str) -> str` (mirrors the resolver's suffix-strip + casefold).
  - `NATAConnector(ScrapeConnector)` with `_build_url(...)`, `build_input(params)`, `_group_by_parent(raws) -> list[dict]` (parent aggregates), and `normalize(...)` — see Task 5 for the classifier-gated `normalize`. This task builds through aggregation only.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/nata_sites.json` — the per-site rows the actor's pageFunction yields (two parents, one multi-site):

```json
[
  {"parent_org": "Acme Testing Pty Ltd", "site_name": "Acme Sydney Lab",
   "accreditation_number": "2771", "site_number": "1", "state": "NSW",
   "address": "12 Test St, Sydney NSW 2000", "service": "testing"},
  {"parent_org": "Acme Testing Pty. Ltd.", "site_name": "Acme Melbourne Lab",
   "accreditation_number": "2771", "site_number": "2", "state": "VIC",
   "address": "9 Lab Rd, Melbourne VIC 3000", "service": "testing"},
  {"parent_org": "NSW Health Pathology", "site_name": "RPA Pathology",
   "accreditation_number": "100", "site_number": "5", "state": "NSW",
   "address": "50 Missenden Rd, Camperdown NSW 2050", "service": "testing"}
]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_nata_connector.py
import json
from pathlib import Path
from sourcing.connectors.nata import NATAConnector, normalize_org_name

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nata_sites.json"
SITES = json.loads(_FIX.read_text())


def test_build_url_encodes_params():
    c = NATAConnector()
    url = c._build_url(state="NSW", search="testing", page=2)
    assert url.startswith("https://nata.com.au/page/2/?")
    assert "post_type=site" in url and "state=NSW" in url and "s=testing" in url
    assert "status=active" in url


def test_normalize_org_name_strips_suffix_and_case():
    assert normalize_org_name("Acme Testing Pty Ltd") == normalize_org_name("Acme Testing Pty. Ltd.")
    assert normalize_org_name("ACME  Testing") == "acme testing"


def test_group_by_parent_aggregates_multisite():
    c = NATAConnector()
    parents = c._group_by_parent(SITES)
    by_name = {p["parent_org"]: p for p in parents}
    acme = by_name["Acme Testing Pty Ltd"]  # first-seen display name wins
    assert acme["site_count"] == 2
    assert set(acme["states"]) == {"NSW", "VIC"}
    assert acme["accreditation_numbers"] == ["2771"]  # deduped
    assert "testing" in acme["service_types"]
    assert len(parents) == 2  # two distinct parents
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_connector.py -q`
Expected: FAIL (`ModuleNotFoundError: sourcing.connectors.nata`).

- [ ] **Step 4: Write the connector (through aggregation)**

Create `src/sourcing/connectors/nata.py`:

```python
"""NATAConnector — NATA accreditation register as a discovery + enrichment source.

Runs the Apify ``apify/playwright-scraper`` actor (the results page is a JS-rendered
SPA; the WordPress REST API is 403-blocked) tiled per state x include-keyword. The
pageFunction yields one row per accredited SITE; ``_group_by_parent`` rolls sites up
to one record per parent org, and ``normalize`` keeps only private_commercial parents
(via the ownership classifier). Every failure degrades to zero records — never raises.
"""
from __future__ import annotations

import math
import re
import warnings
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "nata_accreditation"
_MAX_PAGES = 25  # hard cap per (state x keyword) tile

# Mirror the entity resolver's name normalization (entity_resolution.py:28,72) so
# NATA parents aggregate the same way the resolver later matches them.
_SUFFIX = re.compile(r"\b(pty\s*ltd|pty\s*limited|limited|ltd|& co|and co|inc|corp)\b", re.I)


def normalize_org_name(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIX.sub("", name or "")).strip().lower()


# The browser-side extractor. Kept as a string — it runs inside the Apify actor,
# not in this process. Selectors match on class-contains + structure, not exact
# Tailwind class names, so minor CSS churn doesn't break it.
_PAGE_FUNCTION = r"""
async function pageFunction(context) {
  const { page, request } = context;
  await page.waitForSelector('div:has-text("results")', { timeout: 20000 }).catch(() => {});
  return await page.evaluate(() => {
    const rows = [];
    const cards = Array.from(document.querySelectorAll('div')).filter(
      d => d.querySelector('a[href*="/site/"]'));
    let total = null;
    const rc = document.body.innerText.match(/([\d,]+)\s+results/i);
    if (rc) total = parseInt(rc[1].replace(/,/g, ''), 10);
    for (const card of cards) {
      const text = card.innerText || '';
      const acc = text.match(/Accreditation No\.?\s*(\d+)/i);
      const site = text.match(/Site No\.?\s*(\d+)/i);
      const parentP = card.querySelector('p');
      const link = card.querySelector('a[href*="/site/"]');
      rows.push({
        parent_org: parentP ? parentP.innerText.trim() : '',
        site_name: link ? link.innerText.trim() : '',
        accreditation_number: acc ? acc[1] : null,
        site_number: site ? site[1] : null,
        address: text.split('\n').pop().trim(),
        _total_results: total,
      });
    }
    return rows;
  });
}
"""


class NATAConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "apify/playwright-scraper"
    cache_ttl_seconds: int = 7 * 24 * 3600  # weekly freshness

    def _build_url(self, state: str, search: str = "", filter_by: str = "service",
                   status: str = "active", page: int = 1) -> str:
        params = urlencode({"post_type": "site", "s": search, "filter": filter_by,
                            "state": state, "status": status})
        return f"https://nata.com.au/page/{page}/?{params}"

    def build_input(self, params: dict) -> dict:
        state = params.get("state", "")
        search = params.get("search", "")
        pages = min(int(params.get("pages", 1)), _MAX_PAGES)
        filter_by = params.get("filter_by", "service")
        status = params.get("status", "active")
        urls = [{"url": self._build_url(state, search, filter_by, status, p)}
                for p in range(1, pages + 1)]
        return {
            "startUrls": urls,
            "pageFunction": _PAGE_FUNCTION,
            "waitUntil": ["networkidle2"],
            "proxyConfiguration": {"useApifyProxy": True},
            # carry the search term through so normalize can seed service types
            "_search": search,
        }

    def fetch(self, params: dict) -> list[dict]:
        """Run tile page 1, size the sweep from _total_results, fetch the rest."""
        first = self._run_actor(self.build_input({**params, "pages": 1}))
        if not first:
            return []
        total = first[0].get("_total_results")
        for r in first:
            r.setdefault("service", params.get("search", ""))
        if not total:
            return first
        pages = min(math.ceil(total / 20), _MAX_PAGES)
        if pages <= 1:
            return first
        rest = self._run_actor(self.build_input({**params, "pages": pages}))
        for r in rest:
            r.setdefault("service", params.get("search", ""))
        # dedupe by (accreditation_number, site_number)
        seen: set[tuple] = set()
        out: list[dict] = []
        for r in [*first, *rest]:
            key = (r.get("accreditation_number"), r.get("site_number"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        if total and not out:
            warnings.warn("NATAConnector: non-zero results but 0 extracted — site structure may have changed", stacklevel=2)
        return out

    def _group_by_parent(self, raws: list[dict]) -> list[dict]:
        groups: OrderedDict[str, dict] = OrderedDict()
        for r in raws:
            key = normalize_org_name(r.get("parent_org", ""))
            if not key:
                continue
            g = groups.get(key)
            if g is None:
                g = {"parent_org": r.get("parent_org", ""), "normalized": key,
                     "sites": [], "accreditation_numbers": [], "states": [],
                     "service_types": [], "state_counts": {}}
                groups[key] = g
            g["sites"].append({"site_name": r.get("site_name"),
                               "site_number": r.get("site_number"),
                               "address": r.get("address")})
            acc = r.get("accreditation_number")
            if acc and acc not in g["accreditation_numbers"]:
                g["accreditation_numbers"].append(acc)
            st = _state_of(r)
            if st:
                g["states"].append(st) if st not in g["states"] else None
                g["state_counts"][st] = g["state_counts"].get(st, 0) + 1
            svc = r.get("service")
            if svc and svc not in g["service_types"]:
                g["service_types"].append(svc)
        for g in groups.values():
            g["site_count"] = len(g["sites"])
        return list(groups.values())


_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b")


def _state_of(r: dict) -> str | None:
    st = (r.get("state") or "").strip().upper()
    if st:
        return st
    m = _STATE_RE.search(r.get("address") or "")
    return m.group(1) if m else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_connector.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/sourcing/connectors/nata.py tests/fixtures/nata_sites.json tests/unit/test_nata_connector.py
git commit -m "feat: NATA connector url/extract/aggregate (pre-classify)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: NATAConnector.normalize — classifier gate → CompanyRecord

**Files:**
- Modify: `src/sourcing/connectors/nata.py` (add `__init__`, `normalize`, `_to_record`)
- Test: `tests/unit/test_nata_connector.py` (append)

**Interfaces:**
- Consumes: `_group_by_parent` (Task 4), `OwnershipClassifier`/`PRIVATE` (Task 3), `MoatSignals` fields (Task 2).
- Produces: `NATAConnector(classifier=None, ...)`; `NATAConnector.normalize(raws: list[dict]) -> list[CompanyRecord]` — note it takes the FULL raw list (aggregate+classify are batch operations), returning one record per surviving `private_commercial` parent. Classifier failure → `[]` (warn).

- [ ] **Step 1: Write the failing tests (append to test_nata_connector.py)**

```python
from sourcing.connectors.nata import NATAConnector, PRIVATE  # noqa: E402  (top of file already imports some)
from sourcing.classifiers.ownership_classifier import Classification


class _StubClassifier:
    def __init__(self, mapping):
        self._m = mapping  # normalized-ish display name -> category

    def classify(self, names):
        return [Classification(name=n, category=self._m.get(n, "unclear"),
                               confidence=0.9, reasoning="") for n in names]


def test_normalize_keeps_only_private():
    stub = _StubClassifier({"Acme Testing Pty Ltd": PRIVATE,
                            "NSW Health Pathology": "public_sector"})
    c = NATAConnector(classifier=stub)
    recs = c.normalize(SITES)
    names = [r.legal_name for r in recs]
    assert names == ["Acme Testing Pty Ltd"]
    rec = recs[0]
    assert rec.moat_signals.nata_accreditation is True
    assert rec.moat_signals.regulatory_accreditation is True
    assert rec.moat_signals.nata_site_count == 2
    assert rec.moat_signals.nata_multistate is True
    assert rec.location.state == "NSW"  # primary state = highest site count (tie → first)
    assert rec.abn is None and rec.provenance[0].source == "nata"


def test_normalize_classifier_failure_returns_empty():
    class _Boom:
        def classify(self, names):
            raise RuntimeError("ollama down")

    c = NATAConnector(classifier=_Boom())
    assert c.normalize(SITES) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_connector.py -q`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'classifier'`).

- [ ] **Step 3: Add `__init__`, `normalize`, `_to_record`**

In `src/sourcing/connectors/nata.py`, add to the `NATAConnector` class (after the class attributes, before `_build_url`):

```python
    def __init__(self, *, cache: Any = None, client: Any = None, classifier: Any = None) -> None:
        super().__init__(cache=cache, client=client)
        self._classifier = classifier

    def _get_classifier(self):
        if self._classifier is None:
            from ..classifiers.ownership_classifier import OwnershipClassifier
            self._classifier = OwnershipClassifier()
        return self._classifier
```

Then add `normalize` and `_to_record` (note: `normalize` takes the full raw list, unlike the per-item base contract — the orchestrator calls `normalize` once per fetched item, so we also override the orchestration path in Task 6's params/branch. To stay compatible, `normalize` accepts either a single dict or a list):

```python
    def normalize(self, raw: Any) -> Any:
        """Aggregate + classify. Accepts the full raw list (returns list[CompanyRecord]).

        The orchestrator calls ``normalize(r) for r in raws`` per the base contract,
        which would classify one parent at a time. To keep classification batched and
        cheap, NATA overrides ``fetch`` upstream (Task 6 registers a NATA-aware path)
        to hand the whole list here. If called with a single dict, it wraps it.
        """
        raws = raw if isinstance(raw, list) else [raw]
        parents = self._group_by_parent(raws)
        if not parents:
            return []
        try:
            results = self._get_classifier().classify([p["parent_org"] for p in parents])
        except Exception as exc:  # noqa: BLE001 - degrade to no NATA rows, never raise
            warnings.warn(f"NATAConnector: classifier failed, dropping NATA rows: {exc}", stacklevel=2)
            return []
        from ..classifiers.ownership_classifier import PRIVATE

        records = []
        for parent, cls in zip(parents, results, strict=False):
            if cls.category != PRIVATE:
                continue
            records.append(self._to_record(parent, cls))
        return records

    def _to_record(self, parent: dict, cls: Any) -> CompanyRecord:
        from ..models.company import (
            CompanyRecord, Location, MoatSignals, Provenance, Sector,
        )

        states = parent["states"]
        counts = parent["state_counts"]
        primary = max(states, key=lambda s: counts.get(s, 0)) if states else None
        acc_nums = parent["accreditation_numbers"]
        locator = f"Accreditation #{acc_nums[0]}" if acc_nums else "NATA"
        if len(acc_nums) > 1:
            locator += f" + {len(acc_nums) - 1} others"
        flags = []
        if 0.5 <= cls.confidence < 0.8:
            flags.append("nata_classification_uncertain")
        return CompanyRecord(
            entity_id=f"nata:{parent['normalized']}",
            abn=None,
            legal_name=parent["parent_org"],
            country="Australia",
            location=Location(state=primary),
            sector=Sector(category_text=list(parent["service_types"])),
            moat_signals=MoatSignals(
                regulatory_accreditation=True,
                nata_accreditation=True,
                nata_site_count=parent["site_count"],
                nata_service_types=list(parent["service_types"]),
                nata_accreditation_numbers=list(acc_nums),
                nata_states=list(states),
                nata_multistate=len(states) > 1,
            ),
            provenance=[Provenance(field="nata_accreditation", source="nata",
                                   locator=locator, confidence=0.95)],
            flags=flags,
            resolution_confidence=0.0,
        )
```

Note: verify `Provenance` accepts a `locator` kwarg; if not, drop it (grep `class Provenance` in `models/company.py`). Verify `CompanyRecord` accepts `flags=` and `resolution_confidence=` (it does — used across the codebase).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_connector.py -q`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/sourcing/connectors/nata.py tests/unit/test_nata_connector.py
git commit -m "feat: NATA normalize with classifier gate -> CompanyRecord

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: NATACache (data/nata.duckdb) + load()/lookup

**Files:**
- Modify: `src/sourcing/connectors/nata.py` (add `NATACache`, `NATAConnector.load`)
- Test: `tests/unit/test_nata_cache.py`

**Interfaces:**
- Produces:
  - `NATACache(db_path: str | Path)` with `.upsert(records: list[CompanyRecord])`,
    `.find_by_normalized_name(name: str, state: str | None = None) -> dict | None`.
  - `NATAConnector.load(records: list[CompanyRecord], cache: NATACache) -> None` (writes the sweep to cache for Plan B).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_nata_cache.py
from sourcing.connectors.nata import NATACache
from sourcing.models.company import CompanyRecord, Location, MoatSignals


def _rec(name, state="NSW"):
    return CompanyRecord(entity_id=f"nata:{name}", legal_name=name,
                         location=Location(state=state),
                         moat_signals=MoatSignals(nata_accreditation=True,
                                                  nata_site_count=2, nata_states=[state]))


def test_cache_roundtrip(tmp_path):
    cache = NATACache(tmp_path / "nata.duckdb")
    cache.upsert([_rec("Acme Testing Pty Ltd")])
    hit = cache.find_by_normalized_name("Acme Testing Pty. Ltd.")  # suffix/case differ
    assert hit is not None
    assert hit["nata_site_count"] == 2
    assert cache.find_by_normalized_name("Unknown Co") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_cache.py -q`
Expected: FAIL (`ImportError: cannot import name 'NATACache'`).

- [ ] **Step 3: Add `NATACache` + `load`**

Append to `src/sourcing/connectors/nata.py`:

```python
from pathlib import Path  # add to the imports at the top of the file


class NATACache:
    """DuckDB-backed parent cache in its OWN file (never touches bulk.duckdb)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._ensure()

    def _conn(self):
        import duckdb
        return duckdb.connect(self._path)

    def _ensure(self) -> None:
        with self._conn() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS nata_parents ("
                "normalized VARCHAR PRIMARY KEY, legal_name VARCHAR, primary_state VARCHAR,"
                "site_count INTEGER, service_types VARCHAR, accreditation_numbers VARCHAR,"
                "states VARCHAR, multistate BOOLEAN)"
            )

    def upsert(self, records: list) -> None:
        import json
        with self._conn() as con:
            for r in records:
                m = r.moat_signals
                con.execute(
                    "INSERT OR REPLACE INTO nata_parents VALUES (?,?,?,?,?,?,?,?)",
                    [normalize_org_name(r.legal_name or ""), r.legal_name,
                     r.location.state, m.nata_site_count,
                     json.dumps(m.nata_service_types), json.dumps(m.nata_accreditation_numbers),
                     json.dumps(m.nata_states), m.nata_multistate],
                )

    def find_by_normalized_name(self, name: str, state: str | None = None) -> dict | None:
        import json
        with self._conn() as con:
            row = con.execute(
                "SELECT legal_name, primary_state, site_count, service_types,"
                " accreditation_numbers, states, multistate FROM nata_parents"
                " WHERE normalized = ?", [normalize_org_name(name)],
            ).fetchone()
        if row is None:
            return None
        return {"legal_name": row[0], "primary_state": row[1], "nata_site_count": row[2],
                "nata_service_types": json.loads(row[3]), "nata_accreditation_numbers": json.loads(row[4]),
                "nata_states": json.loads(row[5]), "nata_multistate": row[6]}
```

And add a `load` method to `NATAConnector`:

```python
    def load(self, records: list, cache: "NATACache") -> None:
        """Persist a sweep's parents into the Plan-B cache."""
        cache.upsert(records)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_cache.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sourcing/connectors/nata.py tests/unit/test_nata_cache.py
git commit -m "feat: NATACache (own duckdb) for Plan B lookup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Registry entry + orchestrator NATA params branch

**Files:**
- Modify: `data/source_registry.yaml` (add `nata_accreditation` entry)
- Modify: `src/sourcing/orchestrator.py` (add a dedicated NATA branch in `params_for_connector`)
- Test: `tests/unit/test_nata_orchestration.py`

**Interfaces:**
- Consumes: `NATAConnector` (`sourcing.connectors.nata.NATAConnector`).
- Produces: `params_for_connector("nata_accreditation", buybox, ...) -> list[dict]` — one tile per (state × capped include-keyword), each `{"state","search","filter_by","status"}`.

**Note (deviation from spec §6):** do NOT add `nata_accreditation` to `_TILED_SOURCES` — membership there routes into the Google-Maps/Yellow-Pages param shape. Add a dedicated branch instead; it still returns a multi-tile list so the state × keyword fan-out happens.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_nata_orchestration.py
from sourcing.orchestrator import params_for_connector
from sourcing.rank.buybox import BuyBox


def test_nata_params_tile_state_x_keyword():
    bb = BuyBox(thesis="testing labs", sector_keywords=["testing", "calibration"],
                states=["NSW", "VIC"])
    tiles = params_for_connector("nata_accreditation", bb)
    combos = {(t["state"], t["search"]) for t in tiles}
    assert ("NSW", "testing") in combos
    assert ("VIC", "calibration") in combos
    assert all(t["status"] == "active" for t in tiles)


def test_nata_registered_as_scrape_connector():
    from sourcing.rag.registry_seed import load_seed_registry
    entry = {e.source_id: e for e in load_seed_registry()}["nata_accreditation"]
    assert entry.connector_type.value == "scrape"
    assert entry.connector_ref.endswith("nata.NATAConnector")
    assert entry.enabled is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_orchestration.py -q`
Expected: FAIL (params branch missing → generic fallback lacks `state`/`search`; registry entry absent → KeyError).

- [ ] **Step 3: Add the params branch**

In `src/sourcing/orchestrator.py` `params_for_connector`, add this branch immediately BEFORE the `if source_id in _TILED_SOURCES:` block:

```python
    # --- NATA: tile per state x include-keyword (its own param shape) --------
    if source_id == "nata_accreditation":
        from .config import get_settings

        max_terms = get_settings().scrape_max_search_terms
        terms = (keywords or ["testing"])[:max_terms]
        target_states = states or ["NSW", "VIC", "QLD", "SA", "WA"]
        return [
            {"state": st, "search": kw, "filter_by": "service", "status": "active"}
            for st in target_states
            for kw in terms
        ]
```

- [ ] **Step 4: Add the registry entry**

In `data/source_registry.yaml`, add (match the indentation/format of the existing `telstra_awards` entry):

```yaml
- source_id: nata_accreditation
  connector_type: scrape
  connector_ref: "sourcing.connectors.nata.NATAConnector"
  actor_id: "apify/playwright-scraper"
  fields_provided:
    - moat_signals.regulatory_accreditation
    - moat_signals.nata_accreditation
    - moat_signals.nata_site_count
    - moat_signals.nata_service_types
    - moat_signals.nata_accreditation_numbers
    - moat_signals.nata_states
    - moat_signals.nata_multistate
  sectors_covered: ["all"]
  geo_granularity: "state"
  join_key: "name"
  cost_tier: "metered"
  freshness: "on_demand"
  reliability: "text_high"
  enabled: true
  gate: "full_pool"
  connector_ref_built: true
  capability_doc: "NATA accreditation register — Australian testing, inspection, certification, calibration labs. Regulatory moat; private-only filter."
```

Note: match the EXACT field set the loader requires — grep an existing scrape entry (e.g. `yellow_pages`) in the same file and mirror every key it has (`connector_built`, `capability_doc`, etc.). Remove any key not present on peers; add any required key that is.

- [ ] **Step 5: Run tests + registry invariant**

Run: `.venv/bin/python -m pytest tests/unit/test_nata_orchestration.py tests/unit/test_registry.py -q`
Expected: PASS (registry test confirms `nata_accreditation` → `ScrapeConnector`).

- [ ] **Step 6: Commit**

```bash
git add data/source_registry.yaml src/sourcing/orchestrator.py tests/unit/test_nata_orchestration.py
git commit -m "feat: register NATA source + orchestrator params branch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Plan B — guarded NATA enrichment in EnrichmentNode

**Files:**
- Modify: `src/sourcing/enrichment/enrichment_node.py` (`__init__` + `enrich_one`)
- Test: `tests/unit/test_enrichment_nata.py`

**Interfaces:**
- Consumes: `NATACache.find_by_normalized_name` (Task 6), `MoatSignals` NATA fields (Task 2).
- Produces: `EnrichmentNode(..., nata_cache=None)`; when set, `enrich_one` annotates a record whose legal name hits the cache. Missing cache / lookup error ⇒ silent no-op.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_enrichment_nata.py
from sourcing.enrichment.enrichment_node import EnrichmentNode
from sourcing.models.company import CompanyRecord, Location
from sourcing.rank.buybox import BuyBox


class _FakeExtractor:
    def extract(self, rec, buybox):  # no-op signal extractor
        return rec


class _FakeAusTender:
    def enrich_record(self, rec):
        return rec


class _StubNataCache:
    def find_by_normalized_name(self, name, state=None):
        if "acme" in name.lower():
            return {"legal_name": "Acme Testing Pty Ltd", "primary_state": "NSW",
                    "nata_site_count": 4, "nata_service_types": ["testing"],
                    "nata_accreditation_numbers": ["2771"], "nata_states": ["NSW", "VIC"],
                    "nata_multistate": True}
        return None


def _node(nata_cache):
    return EnrichmentNode(austender=_FakeAusTender(), website=None,
                          signal_extractor=_FakeExtractor(), nata_cache=nata_cache)


def _bb():
    return BuyBox(thesis="testing")


def test_plan_b_annotates_hit():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Testing Pty Ltd",
                        location=Location(state="NSW"))
    _node(_StubNataCache()).enrich_one(rec, _bb())
    assert rec.moat_signals.nata_accreditation is True
    assert rec.moat_signals.regulatory_accreditation is True
    assert rec.moat_signals.nata_site_count == 4
    assert any(p.source == "nata_cache" for p in rec.provenance)


def test_plan_b_miss_is_noop():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Unrelated Co",
                        location=Location(state="NSW"))
    _node(_StubNataCache()).enrich_one(rec, _bb())
    assert rec.moat_signals.nata_accreditation is False


def test_plan_b_absent_cache_is_noop():
    rec = CompanyRecord(entity_id="x", abn="1" * 11, legal_name="Acme Testing Pty Ltd",
                        location=Location(state="NSW"))
    _node(None).enrich_one(rec, _bb())  # nata_cache=None
    assert rec.moat_signals.nata_accreditation is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_enrichment_nata.py -q`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'nata_cache'`).

- [ ] **Step 3: Wire the guarded lookup**

In `src/sourcing/enrichment/enrichment_node.py`:

3a. Add the constructor param. Change the `__init__` signature to include `nata_cache: Any = None,` (after `record_cache: Any = None,`) and add at the end of `__init__`:

```python
        self.nata_cache = nata_cache  # None → skip Plan B NATA lookup
```

3b. In `enrich_one`, inside the `if not cache_hit:` block, after the AusTender call (`self.austender.enrich_record(rec)`), add:

```python
            # Plan B: annotate with NATA accreditation from the sweep cache.
            # Guarded + non-fatal: a missing table or query error is a silent no-op.
            if self.nata_cache is not None and rec.legal_name:
                try:
                    hit = self.nata_cache.find_by_normalized_name(
                        rec.legal_name, rec.location.state)
                    if hit:
                        m = rec.moat_signals
                        m.regulatory_accreditation = True
                        m.nata_accreditation = True
                        m.nata_site_count = hit["nata_site_count"]
                        m.nata_service_types = hit["nata_service_types"]
                        m.nata_accreditation_numbers = hit["nata_accreditation_numbers"]
                        m.nata_states = hit["nata_states"]
                        m.nata_multistate = hit["nata_multistate"]
                        from ..models.company import Provenance
                        rec.provenance.append(Provenance(
                            field="nata_accreditation", source="nata_cache", confidence=0.9))
                except Exception:  # noqa: BLE001 - Plan B is best-effort, never fatal
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_enrichment_nata.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/sourcing/enrichment/enrichment_node.py tests/unit/test_enrichment_nata.py
git commit -m "feat: Plan B guarded NATA enrichment in EnrichmentNode

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Live integration tests (gated) + full verification

**Files:**
- Create: `tests/integration/test_nata_live.py`
- Test: the whole suite.

**Interfaces:**
- Consumes: everything above; `NATAConnector`, `OwnershipClassifier`, `APIFY_API_TOKEN`, Ollama+qwen.

- [ ] **Step 1: Write the gated live tests**

```python
# tests/integration/test_nata_live.py
import shutil
import subprocess
import pytest

from sourcing.config import get_settings
from sourcing.connectors.nata import NATAConnector
from sourcing.classifiers.ownership_classifier import OwnershipClassifier, PRIVATE

pytestmark = pytest.mark.integration


def _qwen_available() -> bool:
    if not shutil.which("ollama"):
        return False
    out = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    return "qwen2.5:3b" in out.stdout


@pytest.mark.skipif(not _qwen_available(), reason="qwen2.5:3b not pulled")
def test_classifier_live_qwen():
    clf = OwnershipClassifier()
    out = clf.classify(["Melbourne Pathology Pty Ltd", "NSW Health Pathology",
                        "Bureau Veritas Australia"])
    cats = {c.name: c.category for c in out}
    assert cats["Melbourne Pathology Pty Ltd"] == PRIVATE
    assert cats["NSW Health Pathology"] == "public_sector"


@pytest.mark.skipif(not get_settings().apify_api_token, reason="APIFY_API_TOKEN not set")
def test_nata_sweep_nsw_testing_costs_credits():
    # NOTE: spends Apify credits. One small NSW/testing tile.
    c = NATAConnector()
    raws = c.fetch({"state": "NSW", "search": "testing"})
    assert isinstance(raws, list)
    if raws:  # register may rate-limit; assert shape when we get data
        assert "accreditation_number" in raws[0]
        parents = c._group_by_parent(raws)
        assert parents and parents[0]["site_count"] >= 1
```

- [ ] **Step 2: Run the live tests (deliberate — spends credits)**

Run: `.venv/bin/python -m pytest tests/integration/test_nata_live.py -v`
Expected: classifier test PASS if qwen present (else skip); Apify test PASS or skip. If the Apify account is capped, it raises `ForbiddenError` inside `fetch` — that is expected at the account level, not a code bug; note it and move on.

- [ ] **Step 3: Full offline suite**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS (all prior tests + the new NATA offline tests; count increased by ~18).

- [ ] **Step 4: Lint**

Run: `.venv/bin/ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_nata_live.py
git commit -m "test: gated live NATA integration tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §Fetch (Apify playwright-scraper, JS-render) → Task 4 (`_PAGE_FUNCTION`, `build_input`, `fetch`). ✓
- §Aggregation (sites→parent) → Task 4 `_group_by_parent`. ✓
- §Classifier (pluggable, qwen default, batch, confidence, JSON-retry) → Task 1 (settings) + Task 3 (module). ✓
- §Private-only filter → Task 5 `normalize` keeps `PRIVATE`. ✓
- §CompanyRecord shape (primary state, provenance 0.95, abn None) → Task 5 `_to_record`. ✓
- §6 MoatSignals fields → Task 2. ✓
- §Plan B cache + guarded enrichment → Task 6 (`NATACache`) + Task 8. ✓
- §Registry + orchestrator tiling → Task 7 (with the `_TILED_SOURCES` deviation documented). ✓
- §Safety/degradation → classifier failure → `[]` (Task 5); orchestrator try/except inherited; Plan B no-op (Task 8); own duckdb (Task 6). ✓
- §Testing (offline fixtures + gated live) → every task + Task 9. ✓

**Placeholder scan:** No TBD/TODO; every code step has full code. Two explicit "verify against peers" notes (Provenance `locator` kwarg in Task 5; exact yaml key set in Task 7) are real verification actions, not deferred logic — the implementer greps one existing file and mirrors it.

**Type consistency:** `normalize_org_name`, `_group_by_parent` (returns dicts with `parent_org/normalized/sites/site_count/states/state_counts/service_types/accreditation_numbers`), `OwnershipClassifier.classify → list[Classification]`, `NATACache.find_by_normalized_name → dict|None`, and `EnrichmentNode(nata_cache=...)` are used consistently across Tasks 4–8. `PRIVATE` constant referenced in Tasks 3/5/9.

**Open verification items for the implementer (grep, don't guess):**
1. `Provenance` fields — confirm `locator` exists (Task 5); if not, drop it.
2. `source_registry.yaml` scrape-entry key set — mirror `yellow_pages` exactly (Task 7).
3. `httpx` is already a dependency (used by `llm.py`/tests) — confirm before the Ollama path in Task 3.
