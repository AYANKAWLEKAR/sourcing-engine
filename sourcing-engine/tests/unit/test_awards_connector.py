"""Offline unit tests for the Telstra award-register AgentConnector.

No network / no LLM: the Apify client returns fixed page markdown and the LLM client
returns a fixed JSON category object. Names/states are parsed structurally; categories
come from the (faked) LLM in JSON mode. Also guards the base_agent `_default_extractor`.
"""
from __future__ import annotations

from sourcing.connectors.awards import TelstraAwardsConnector
from sourcing.connectors.base_agent import AgentConnector
from sourcing.llm import LLMResponse

# A miniature award page in the real "#### name / STATE Finalist / blurb" shape.
_PAGE_MD = """
# 2025 State Finalists — Embracing Innovation

#### Comtrac

QLD Finalist

Comtrac builds compliance and case-management software for regulators.

#### Big Bag Recovery

ACT Finalist

Big Bag Recovery runs a plastic packaging recycling program.

#### Chatstat

qld Finalist

Chatstat is an AI child-safety monitoring tool for families.
"""

# Three finalists → a categories list of length 3, aligned by index.
_CATEGORIES_JSON = '{"categories": ["software provider", "waste recycling", "AI safety software"]}'


class FakeApify:
    def actor(self, actor_id):
        class _A:
            def call(self, run_input, **kw):
                class _R:
                    default_dataset_id = "ds1"

                return _R()

        return _A()

    def dataset(self, dsid):
        class _D:
            def list_items(self):
                class _Res:
                    items = [{"markdown": _PAGE_MD}]

                return _Res()

        return _D()


class FakeLLM:
    """LLMClient returning a fixed JSON categories object."""

    def __init__(self, json_text):
        self._text = json_text
        self.calls = []

    def chat(self, model, system, messages, tools=None, format=None):
        self.calls.append({"model": model, "format": format})
        return LLMResponse(text=self._text)


def _connector(categories_json=_CATEGORIES_JSON):
    return TelstraAwardsConnector(client=FakeApify(), llm_client=FakeLLM(categories_json))


# ---------------------------------------------------------------------------
# Conformance + fetch
# ---------------------------------------------------------------------------

def test_inherits_agentconnector():
    assert isinstance(TelstraAwardsConnector(), AgentConnector)


def test_fetch_parses_names_states_and_classifies():
    fake_llm = FakeLLM(_CATEGORIES_JSON)
    c = TelstraAwardsConnector(client=FakeApify(), llm_client=fake_llm)
    recs = c.fetch({"year": 2025, "categories": ["embracing-innovation"]})

    assert [r["org_name"] for r in recs] == ["Comtrac", "Big Bag Recovery", "Chatstat"]
    assert [r["state"] for r in recs] == ["QLD", "ACT", "QLD"]        # 'qld' normalized
    assert [r["raw"]["category"] for r in recs] == [
        "software provider", "waste recycling", "AI safety software"
    ]
    # Category classification runs in JSON mode (fast + reliable on Claude).
    assert fake_llm.calls and fake_llm.calls[0]["format"] == "json"


def test_fetch_sweeps_all_categories_by_default():
    fake_llm = FakeLLM('{"categories": ["x", "y", "z"]}')
    c = TelstraAwardsConnector(client=FakeApify(), llm_client=fake_llm)
    c.fetch({})  # no categories → one classify call per default slug
    assert len(fake_llm.calls) == len(TelstraAwardsConnector.category_slugs)


def test_fetch_tolerates_short_category_list():
    # Fewer categories than finalists → missing ones fall back to None (no crash).
    c = TelstraAwardsConnector(client=FakeApify(), llm_client=FakeLLM('{"categories": ["only one"]}'))
    recs = c.fetch({"categories": ["embracing-innovation"]})
    assert [r["raw"]["category"] for r in recs] == ["only one", None, None]


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def test_normalize_sets_award_signal_and_category():
    c = _connector()
    rec = c.normalize(c.fetch({"categories": ["embracing-innovation"]})[0])

    assert rec.entity_id == "award:telstra:comtrac"
    assert rec.legal_name == "Comtrac"
    assert rec.location.state == "QLD"
    assert rec.abn is None                                    # resolved downstream
    assert rec.moat_signals.award_finalist is True
    assert rec.sector.category_text == ["software provider"]  # LLM category feeds sector

    sig = rec.award_signals[0]
    assert sig.program == "Telstra Best of Business"
    assert sig.tier == 1
    assert sig.year == 2025
    assert sig.category == "software provider"


def test_normalize_provenance_separates_fact_from_inference():
    c = _connector()
    rec = c.normalize(c.fetch({"categories": ["embracing-innovation"]})[0])
    conf = {p.field: p.confidence for p in rec.provenance}
    assert conf["award_finalist"] == 0.9   # verbatim page fact
    assert conf["sector"] == 0.5           # LLM classification


# ---------------------------------------------------------------------------
# base_agent._default_extractor — enrich_model + JSON mode
# ---------------------------------------------------------------------------

def test_default_extractor_uses_enrich_model_json(monkeypatch):
    from sourcing.config import get_settings

    class FakeJSONLLM:
        def __init__(self):
            self.last = None

        def chat(self, model, system, messages, tools=None, format=None):
            self.last = {"model": model, "format": format}
            return LLMResponse(text='{"ok": true}')

    fake = FakeJSONLLM()
    monkeypatch.setattr("sourcing.llm.get_llm_client", lambda settings=None: fake)

    extractor = TelstraAwardsConnector()._default_extractor()
    out = extractor("prompt", "content")

    assert out == {"ok": True}
    assert fake.last["format"] == "json"
    assert fake.last["model"] == get_settings().enrich_model
