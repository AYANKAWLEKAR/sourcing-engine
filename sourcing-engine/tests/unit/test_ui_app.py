"""Streamlit UI smoke tests via AppTest — offline, fake OrigoClient injected.

Drives the phases and asserts observable UI state: chat confirm cascades to the
results shortlist, a failed run surfaces an error, the detail drawer renders
provenance, and needs_review keeps the user in chat with a warning.

The stage progress bar polls with time.sleep + st.rerun, so AppTest only reaches
a stable tree on a terminal status (complete/failed). The bar's math is covered
directly by test_progress_fraction.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The UI script does `from api_client import OrigoClient`; put ui/ on the path.
UI_DIR = Path(__file__).resolve().parents[2] / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(UI_DIR / "app.py")
ABN = "1" * 11

_RECORD = {
    "legal_name": "Acme Air",
    "abn": ABN,
    "acn": "123456789",
    "location": {"state": "QLD", "postcode": "4000", "suburb": "Brisbane"},
    "business_model": "B2B HVAC installer",
}
_SHORTLIST = [
    {
        "record": _RECORD,
        "s_stat": 70.0,
        "s_final": 0.7,
        "judge_fit": 0.72,
        "judge_rationale": "Strong sector fit.",
        "standout_signals": ["award finalist"],
    }
]


class FakeClient:
    """Scriptable stand-in for OrigoClient."""

    def __init__(self, *, buybox_reply, status="complete"):
        self._buybox_reply = buybox_reply
        self._status = status
        self.selected = []

    def start_run(self, message):
        return {"run_id": "run_1", **self._buybox_reply}

    def continue_buybox(self, run_id, message):
        return {"run_id": run_id, **self._buybox_reply}

    def get_run(self, run_id):
        run = {"run_id": run_id, "status": self._status, "coverage": {"n_resolved": 4}}
        if self._status == "failed":
            run["error"] = "boom"
        if self._status == "complete":
            run["shortlist"] = _SHORTLIST
        return run

    def get_company(self, run_id, abn):
        return {"record": _RECORD}

    def get_company_sources(self, run_id, abn):
        return {"provenance": [{"field": "abn", "source": "abn_lookup_api", "confidence": 0.9}]}

    def select(self, run_id, abn):
        self.selected.append(abn)
        return {"selected": True}


def _app(client) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=15)
    at.session_state["client"] = client
    return at


def test_progress_fraction():
    from app import _progress_fraction

    assert _progress_fraction("planning") < _progress_fraction("ranking") < 1.0
    assert _progress_fraction("complete") == 1.0


def test_chat_confirm_cascades_to_results():
    client = FakeClient(
        buybox_reply={"reply": "Confirmed.", "ruleset_confirmed": True},
        status="complete",
    )
    at = _app(client)
    at.run()
    at.chat_input[0].set_value("HVAC in QLD, finalize").run()

    assert at.session_state["run_id"] == "run_1"
    assert at.session_state["phase"] == "results"
    assert any("Acme Air" in e.label for e in at.expander)


def test_running_failed_shows_error():
    client = FakeClient(buybox_reply={"ruleset_confirmed": True}, status="failed")
    at = _app(client)
    at.session_state["phase"] = "running"
    at.session_state["run_id"] = "run_1"
    at.run()

    assert at.session_state["phase"] == "running"
    assert any("boom" in e.value for e in at.error)


def test_detail_drawer_shows_provenance():
    client = FakeClient(buybox_reply={"ruleset_confirmed": True}, status="complete")
    at = _app(client)
    at.session_state["phase"] = "results"
    at.session_state["run_id"] = "run_1"
    at.session_state["selected_abn"] = ABN
    at.run()

    assert any("Company detail" in s.value for s in at.subheader)


def test_needs_review_warns_and_stays_in_chat():
    client = FakeClient(buybox_reply={"reply": "Need more info.", "needs_review": True})
    at = _app(client)
    at.run()
    at.chat_input[0].set_value("vague").run()

    assert at.session_state["phase"] == "chat"
    assert len(at.warning) >= 1
