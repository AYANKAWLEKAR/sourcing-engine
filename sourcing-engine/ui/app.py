"""Origo analyst UI (Part D) — a single-column chat, ChatGPT/Claude style.

One clean conversational surface (no tabs, no model/voice chrome):

    * the buy-box conversation renders as chat turns;
    * when the ruleset confirms, the sourcing run streams in as an assistant turn
      with a live, verbose stage trace (planning → acquiring → resolving →
      enriching → ranking, each with counts as they land);
    * the ranked shortlist then renders inline as the final assistant turn.

A slim sidebar carries the live "Buy box" summary and a New search button — the
main column stays pure conversation.

The run still moves through ``st.session_state["phase"]`` (``chat`` → ``running`` →
``results``). The ``OrigoClient`` is read from ``st.session_state["client"]`` when
present (so tests can inject a fake); otherwise a default HTTP client is built.
"""
from __future__ import annotations

import time
from typing import Any

import streamlit as st
from api_client import OrigoClient

# Ordered pipeline stages (mirrors sourcing.models.run.PIPELINE_STAGES). Kept local
# so the UI has no import dependency on the engine package.
PIPELINE_STAGES = ("planning", "acquiring", "resolving", "enriching", "ranking")
STAGE_LABELS = {
    "planning": "Planning sources",
    "acquiring": "Acquiring candidates",
    "resolving": "Resolving to ABNs",
    "enriching": "Enriching (web + signals)",
    "ranking": "Screening, scoring & judging",
}
STAGE_ICON = {"done": "✅", "active": "⏳", "pending": "○"}
POLL_INTERVAL_SECONDS = 1.0


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
    st.session_state.setdefault("ruleset_state", {})  # latest summarize_ruleset() dump
    st.session_state.setdefault("selected_abn", None)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

def _render_messages() -> None:
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])


def _handle_prompt(prompt: str) -> None:
    client = get_client()
    st.session_state["messages"].append({"role": "user", "text": prompt})

    with st.chat_message("assistant"), st.spinner("Thinking…"):
        if st.session_state["run_id"] is None:
            reply = client.start_run(prompt)
            st.session_state["run_id"] = reply["run_id"]
        else:
            reply = client.continue_buybox(st.session_state["run_id"], prompt)

    st.session_state["messages"].append({"role": "assistant", "text": reply.get("reply", "")})
    st.session_state["ruleset_state"] = reply.get("ruleset_state") or {}

    if reply.get("ruleset_confirmed"):
        st.session_state["phase"] = "running"
    elif reply.get("needs_review"):
        st.session_state["chat_warning"] = (
            "Question cap reached without confirmation. Refine and try again."
        )
    st.rerun()


# ---------------------------------------------------------------------------
# Run trace (verbose loading)
# ---------------------------------------------------------------------------

def _progress_fraction(status: str) -> float:
    if status == "complete":
        return 1.0
    if status in PIPELINE_STAGES:
        return (PIPELINE_STAGES.index(status) + 1) / (len(PIPELINE_STAGES) + 1)
    return 0.0


def _stage_state(status: str, stage: str) -> str:
    if status == "complete":
        return "done"
    if status not in PIPELINE_STAGES:
        return "pending"
    ci, si = PIPELINE_STAGES.index(status), PIPELINE_STAGES.index(stage)
    if si < ci:
        return "done"
    return "active" if si == ci else "pending"


def _stage_detail(stage: str, run: dict[str, Any]) -> str:
    cov = run.get("coverage") or {}
    if stage == "planning":
        plan = run.get("source_plan") or []
        if plan:
            names = ", ".join(p.get("source_id", "?") for p in plan[:6])
            return f"planned {len(plan)} sources — {names}"
    elif stage == "acquiring":
        if "n_raw" in cov:
            return f"{cov['n_raw']} raw candidates → {cov.get('n_pool', '?')} after dedup"
    elif stage == "resolving":
        if "n_resolved" in cov:
            return f"{cov['n_resolved']} matched to an ABN"
    elif stage == "enriching":
        return "fetching websites & extracting signals"
    elif stage == "ranking":
        if "n_shortlist" in cov:
            return f"{cov['n_shortlist']} shortlisted"
    return ""


def _render_run_trace(run: dict[str, Any], status: str) -> None:
    st.markdown("**Sourcing run**")
    st.progress(_progress_fraction(status), text=f"Stage: {status}")
    for stage in PIPELINE_STAGES:
        state = _stage_state(status, stage)
        line = f"{STAGE_ICON[state]} **{STAGE_LABELS[stage]}**"
        detail = _stage_detail(stage, run)
        if detail:
            line += f" — {detail}"
        st.markdown(line)

    cov = run.get("coverage") or {}
    if cov:
        items = [(k, v) for k, v in cov.items() if v is not None]
        if items:
            cols = st.columns(len(items))
            for col, (name, value) in zip(cols, items, strict=False):
                col.metric(name.replace("n_", "").replace("_", " "), value)


def _render_running() -> None:
    client = get_client()
    run = client.get_run(st.session_state["run_id"])
    status = run.get("status", "planning")

    with st.chat_message("assistant"):
        if status == "failed":
            st.error(f"Run failed: {run.get('error') or 'unknown error'}")
            return
        _render_run_trace(run, status)

    if status == "complete":
        st.session_state["phase"] = "results"
        st.rerun()
        return

    time.sleep(POLL_INTERVAL_SECONDS)
    st.rerun()


# ---------------------------------------------------------------------------
# Results (ranked shortlist, inline)
# ---------------------------------------------------------------------------

def _render_results() -> None:
    client = get_client()
    run = client.get_run(st.session_state["run_id"])
    shortlist = run.get("shortlist") or []

    with st.chat_message("assistant"):
        st.markdown(f"**Ranked shortlist — {len(shortlist)} companies**")
        if not shortlist:
            st.info("No companies matched the buy box.")
            return

        query = st.text_input("Filter by name or state", "").strip().lower()
        rows = _shortlist_rows(shortlist)
        if query:
            rows = [r for r in rows if query in r["name"].lower() or query in r["state"].lower()]
        rows.sort(key=lambda r: r["s_final"], reverse=True)

        for row in rows:
            with st.expander(f"#{row['rank']}  {row['name']}  ·  S_final {row['s_final']:.3f}"):
                c1, c2, c3 = st.columns(3)
                c1.metric("S_final", f"{row['s_final']:.3f}")
                c2.metric("S_stat", f"{row['s_stat']:.1f}")
                c3.metric(
                    "Judge fit", "—" if row["judge_fit"] is None else f"{row['judge_fit']:.2f}"
                )
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
        with st.chat_message("assistant"):
            _render_detail(client, st.session_state["selected_abn"])


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
    st.markdown("### Company detail")
    company = client.get_company(st.session_state["run_id"], abn)
    rec = company.get("record") or {}
    st.markdown(f"#### {rec.get('legal_name') or '(unnamed)'}")
    st.caption(f"ABN {rec.get('abn') or ''} · ACN {rec.get('acn') or ''}")

    loc = rec.get("location") or {}
    st.write(
        f"**Location:** {loc.get('suburb') or ''} {loc.get('state') or ''} {loc.get('postcode') or ''}"
    )
    if rec.get("business_model"):
        st.write(f"**Business model:** {rec['business_model']}")

    sources = client.get_company_sources(st.session_state["run_id"], abn)
    provenance = sources.get("provenance") or []
    if provenance:
        st.markdown("**Provenance (field · source · confidence)**")
        st.table(
            [
                {"field": p.get("field"), "source": p.get("source"), "confidence": p.get("confidence")}
                for p in provenance
            ]
        )

    if st.button("Close detail"):
        st.session_state["selected_abn"] = None
        st.rerun()


# ---------------------------------------------------------------------------
# Sidebar — live buy-box summary + reset
# ---------------------------------------------------------------------------

def _fmt_logic(logic: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in logic.items())


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Buy box")
        state = st.session_state.get("ruleset_state") or {}
        if not state:
            st.caption("Describe your buy box to begin.")
        else:
            st.markdown("Sector: " + ("✅ resolved" if state.get("sector_resolved") else "⬜ not yet"))
            st.markdown(
                "Geography: " + ("✅ resolved" if state.get("geography_resolved") else "⬜ not yet")
            )
            if state.get("sector_keywords"):
                st.caption("Keywords: " + ", ".join(state["sector_keywords"][:8]))
            if state.get("states"):
                st.caption("States: " + ", ".join(state["states"]))
            settings = state.get("settings") or {}
            if settings:
                for f, logic in settings.items():
                    st.caption(f"• {f}: {_fmt_logic(logic)}")
            missing = state.get("missing") or []
            if missing:
                st.markdown("**Still required:**")
                for m in missing:
                    st.caption(f"– {m}")
            elif state.get("confirmed"):
                st.success("Confirmed — run launched.")

        st.divider()
        if st.button("＋ New search", use_container_width=True):
            _reset()


def _reset() -> None:
    client = st.session_state.get("client")
    st.session_state.clear()
    if client is not None:
        st.session_state["client"] = client
    init_state()
    st.rerun()


# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Off-Market Sourcing", layout="centered")
    init_state()
    _render_sidebar()

    st.title("Off-Market Sourcing")

    warning = st.session_state.pop("chat_warning", None)
    if warning:
        st.warning(warning)

    _render_messages()

    phase = st.session_state["phase"]
    if phase == "running":
        _render_running()
    elif phase == "results":
        _render_results()

    if phase == "chat":
        prompt = st.chat_input("Describe your buy box — e.g. Founder-owned HVAC installers in Sydney, $1–5M EBITDA")
        if prompt:
            _handle_prompt(prompt)


main()
