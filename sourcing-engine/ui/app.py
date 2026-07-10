"""Origo analyst UI (Part D) — buy-box chat -> run progress -> ranked shortlist.

A single Streamlit page over the FastAPI run API. Three phases, driven by
``st.session_state["phase"]``:

    chat      multi-turn buy-box conversation (POST /runs, /runs/{id}/buybox)
    running   stage-level loading bar (poll GET /runs/{id})
    results   interactive ranked shortlist + company detail drawer

The ``OrigoClient`` is read from ``st.session_state["client"]`` when present
(so tests can inject a fake); otherwise a default HTTP client is built.
"""
from __future__ import annotations

import time
from typing import Any

import streamlit as st
from api_client import OrigoClient

# Ordered pipeline stages (mirrors sourcing.models.run.PIPELINE_STAGES) used to
# turn a run status into a progress fraction. Kept local so the UI has no import
# dependency on the engine package.
PIPELINE_STAGES = ("planning", "acquiring", "resolving", "enriching", "ranking")
POLL_INTERVAL_SECONDS = 2.0


def get_client() -> OrigoClient:
    client = st.session_state.get("client")
    if client is None:
        client = OrigoClient()
        st.session_state["client"] = client
    return client


def init_state() -> None:
    st.session_state.setdefault("phase", "chat")
    st.session_state.setdefault("run_id", None)
    st.session_state.setdefault("messages", [])  # [{role, text}]
    st.session_state.setdefault("selected_abn", None)


# ---------------------------------------------------------------------------
# Phase 1 — buy-box chat
# ---------------------------------------------------------------------------

def render_chat() -> None:
    st.subheader("Describe your buy box")
    # A warning set on the previous run survives the rerun via session_state.
    warning = st.session_state.pop("chat_warning", None)
    if warning:
        st.warning(warning)

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])

    prompt = st.chat_input("e.g. Founder-owned HVAC installers in QLD, $1-15M EBITDA")
    if not prompt:
        return

    client = get_client()
    st.session_state["messages"].append({"role": "user", "text": prompt})

    with st.spinner("Agent is thinking…"):
        if st.session_state["run_id"] is None:
            reply = client.start_run(prompt)
            st.session_state["run_id"] = reply["run_id"]
        else:
            reply = client.continue_buybox(st.session_state["run_id"], prompt)

    st.session_state["messages"].append({"role": "assistant", "text": reply.get("reply", "")})

    if reply.get("ruleset_confirmed"):
        st.session_state["phase"] = "running"
    elif reply.get("needs_review"):
        st.session_state["chat_warning"] = (
            "Question cap reached without confirmation. Refine and try again."
        )
    st.rerun()


# ---------------------------------------------------------------------------
# Phase 2 — run progress
# ---------------------------------------------------------------------------

def _progress_fraction(status: str) -> float:
    if status == "complete":
        return 1.0
    if status in PIPELINE_STAGES:
        return (PIPELINE_STAGES.index(status) + 1) / (len(PIPELINE_STAGES) + 1)
    return 0.0


def render_running() -> None:
    st.subheader("Sourcing run in progress")
    client = get_client()
    run = client.get_run(st.session_state["run_id"])
    status = run.get("status", "planning")

    if status == "failed":
        st.error(f"Run failed: {run.get('error') or 'unknown error'}")
        if st.button("Start over"):
            _reset()
        return

    if status == "complete":
        st.session_state["phase"] = "results"
        st.rerun()
        return

    st.progress(_progress_fraction(status), text=f"Stage: {status}")

    coverage = run.get("coverage") or {}
    if coverage:
        cols = st.columns(len(coverage))
        for col, (name, value) in zip(cols, coverage.items(), strict=False):
            col.metric(name.replace("_", " "), value)

    time.sleep(POLL_INTERVAL_SECONDS)
    st.rerun()


# ---------------------------------------------------------------------------
# Phase 3 — ranked shortlist + detail
# ---------------------------------------------------------------------------

def render_results() -> None:
    client = get_client()
    run = client.get_run(st.session_state["run_id"])
    shortlist = run.get("shortlist") or []

    st.subheader(f"Ranked shortlist — {len(shortlist)} companies")
    if not shortlist:
        st.info("No companies in the shortlist.")
        if st.button("Start over"):
            _reset()
        return

    query = st.text_input("Filter by name or state", "").strip().lower()
    rows = _shortlist_rows(shortlist)
    if query:
        rows = [r for r in rows if query in r["name"].lower() or query in r["state"].lower()]

    rows.sort(key=lambda r: r["s_final"], reverse=True)

    for row in rows:
        with st.expander(
            f"#{row['rank']}  {row['name']}  ·  S_final {row['s_final']:.3f}"
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("S_final", f"{row['s_final']:.3f}")
            c2.metric("S_stat", f"{row['s_stat']:.1f}")
            c3.metric("Judge fit", "—" if row["judge_fit"] is None else f"{row['judge_fit']:.2f}")
            st.caption(f"ABN {row['abn']} · {row['state']}")
            if row["standout"]:
                st.markdown("**Standout:** " + ", ".join(row["standout"]))
            if row["rationale"]:
                st.markdown(f"_{row['rationale']}_")
            if st.button("View detail", key=f"detail_{row['abn']}"):
                st.session_state["selected_abn"] = row["abn"]
                st.rerun()
            if st.button("Shortlist", key=f"select_{row['abn']}"):
                client.select(st.session_state["run_id"], row["abn"])
                st.success("Added to shortlist.")

    if st.session_state.get("selected_abn"):
        _render_detail(client, st.session_state["selected_abn"])

    if st.button("Start over"):
        _reset()


def _shortlist_rows(shortlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for i, rc in enumerate(shortlist, 1):
        rec = rc.get("record") or {}
        loc = rec.get("location") or {}
        rows.append(
            {
                "rank": i,
                "name": rec.get("legal_name") or "(unnamed)",
                "abn": rec.get("abn") or "",
                "state": loc.get("state") or "",
                "s_final": rc.get("s_final") or 0.0,
                "s_stat": rc.get("s_stat") or 0.0,
                "judge_fit": rc.get("judge_fit"),
                "standout": rc.get("standout_signals") or [],
                "rationale": rc.get("judge_rationale") or "",
            }
        )
    return rows


def _render_detail(client: OrigoClient, abn: str) -> None:
    st.divider()
    st.subheader("Company detail")
    company = client.get_company(st.session_state["run_id"], abn)
    rec = company.get("record") or {}
    st.markdown(f"### {rec.get('legal_name') or '(unnamed)'}")
    st.caption(f"ABN {rec.get('abn') or ''} · ACN {rec.get('acn') or ''}")

    loc = rec.get("location") or {}
    st.write(f"**Location:** {loc.get('suburb') or ''} {loc.get('state') or ''} {loc.get('postcode') or ''}")
    if rec.get("business_model"):
        st.write(f"**Business model:** {rec['business_model']}")

    sources = client.get_company_sources(st.session_state["run_id"], abn)
    provenance = sources.get("provenance") or []
    if provenance:
        st.markdown("**Provenance (field · source · confidence)**")
        st.table(
            [
                {
                    "field": p.get("field"),
                    "source": p.get("source"),
                    "confidence": p.get("confidence"),
                }
                for p in provenance
            ]
        )

    if st.button("Close detail"):
        st.session_state["selected_abn"] = None
        st.rerun()


# ---------------------------------------------------------------------------

def _reset() -> None:
    client = st.session_state.get("client")
    st.session_state.clear()
    if client is not None:
        st.session_state["client"] = client
    init_state()
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="Origo Sourcing", layout="wide")
    st.title("Origo Off-Market Sourcing")
    init_state()

    phase = st.session_state["phase"]
    if phase == "chat":
        render_chat()
    elif phase == "running":
        render_running()
    else:
        render_results()


main()
