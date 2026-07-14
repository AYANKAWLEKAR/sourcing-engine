"""Serve the run API pre-seeded with a completed demo run — for UI verification.

Builds an in-memory store containing one finished run with a varied shortlist
(gov contracts, awards, IP, EBITDA spread, websites) so the whole results UI —
table, gauges, filter buttons, detail drawer, natural-language re-rank — can be
driven without Apify/Claude/Postgres. The NL-query LLM call is stubbed with a
deterministic keyword parser so the check stays fully offline.

    python scripts/serve_demo_ui.py            # API on :8000, seeded run "demo"
"""
from __future__ import annotations

import random

import uvicorn

from sourcing.api import create_app
from sourcing.models.company import (
    AwardSignal,
    CompanyRecord,
    Location,
    MoatSignals,
    Provenance,
    Sector,
    Size,
)
from sourcing.models.ranking import RankedCompany
from sourcing.models.run import RunStatus
from sourcing.rank import pool_query
from sourcing.runs.manager import InlineExecutor, RunManager
from sourcing.runs.store import InMemoryRunStore

SUBURBS = [
    ("Parramatta", "2150"), ("Chatswood", "2067"), ("Penrith", "2750"),
    ("Bondi", "2026"), ("Newtown", "2042"), ("Manly", "2095"),
    ("Liverpool", "2170"), ("Blacktown", "2148"), ("Hornsby", "2077"),
    ("Ryde", "2112"), ("Cronulla", "2230"), ("Castle Hill", "2154"),
]
NAMES = [
    "Cool Breeze HVAC", "Apex Air & Refrigeration", "Summit Climate Control",
    "Harbour City Air", "Metro Ducted Systems", "Precision Heating & Cooling",
    "Coastal Air Solutions", "Ironbark Mechanical", "Blue Mountains Air",
    "Sterling Refrigeration", "Vanguard Climate", "Delta Air Services",
]


def _company(i: int) -> CompanyRecord:
    rng = random.Random(i)
    suburb, pc = SUBURBS[i % len(SUBURBS)]
    abn = str(10000000000 + i * 137)[:11].rjust(11, "0")
    gov = i % 3 == 0
    award = i % 4 == 0
    accred = i % 2 == 0
    ebitda = None if i % 5 == 0 else rng.randint(800_000, 6_000_000)
    rec = CompanyRecord(
        entity_id=f"abn:{abn}",
        abn=abn,
        legal_name=NAMES[i % len(NAMES)] + (f" {i//len(NAMES)+1}" if i >= len(NAMES) else ""),
        location=Location(state="NSW", postcode=pc, suburb=suburb),
        business_model="B2B",
        sector=Sector(anzsic=["3234"], category_text=["Air conditioning contractor"],
                      keyword_hits=["hvac", "air conditioning"], keyword_density=rng.uniform(0.4, 0.95)),
        size=Size(ebitda_est_aud=ebitda, ebitda_confidence=rng.uniform(0.3, 0.9),
                  employee_count=rng.randint(8, 60)),
        moat_signals=MoatSignals(
            gov_contracts=gov,
            gov_contract_value_aud=rng.randint(200_000, 4_000_000) if gov else None,
            gov_contract_count=rng.randint(1, 5) if gov else None,
            regulatory_accreditation=accred,
            award_finalist=award,
            ip=(i % 6 == 0),
            ip_count=rng.randint(1, 4) if i % 6 == 0 else None,
        ),
        contacts_min={"website": f"https://{NAMES[i % len(NAMES)].lower().replace(' ', '').replace('&','and')}.com.au"},
        provenance=[
            Provenance(field="abn", source="abn_lookup_api", confidence=0.95),
            Provenance(field="business_model", source="website_fetch", confidence=0.7),
        ],
    )
    if award:
        rec.award_signals = [AwardSignal(program="Trades Champion", tier=1,
                                         level="winner" if i % 8 == 0 else "finalist")]
    return rec


def _ranked(rec: CompanyRecord, i: int) -> RankedCompany:
    rng = random.Random(1000 + i)
    s_stat = round(rng.uniform(45, 92), 2)
    from sourcing.rank.buybox import BuyBox
    from sourcing.rank.score import evidence_score
    bb = BuyBox(thesis="Founder-owned HVAC installers in Sydney", sector_keywords=["hvac"],
                states=["NSW"], ebitda_min=1_000_000, ebitda_max=5_000_000)
    s_ev = round(evidence_score(rec, bb), 4)
    judge = round(rng.uniform(0.35, 0.9), 3)
    s_final = round(0.40 * (s_stat / 100) + 0.25 * judge + 0.35 * s_ev, 4)
    signals = []
    m = rec.moat_signals
    if m.gov_contracts and m.gov_contract_value_aud:
        signals.append(f"${m.gov_contract_value_aud:,} gov contracts")
    if m.award_finalist:
        signals.append("award finalist")
    if m.regulatory_accreditation:
        signals.append("regulatory accreditation")
    if m.ip:
        signals.append("IP holdings")
    return RankedCompany(
        record=rec, s_stat=s_stat, s_evidence=s_ev, s_final=s_final, judge_fit=judge,
        judge_rationale="Strong sector and geography fit; verify financials in diligence.",
        standout_signals=signals,
        deferred_assessment=["verify EBITDA / financials (no estimate yet)"] if rec.size.ebitda_est_aud is None else [],
    )


def _deterministic_parse(text: str, thesis: str = "", **kw):
    """Offline stand-in for the LLM query parser — keyword → filter/sort."""
    t = text.lower()
    filters = []
    sort_by, order = "s_final", "desc"
    if "gov" in t or "government" in t or "contract" in t:
        filters.append(pool_query.Filter(field="gov_contracts", op="is_true"))
    if "award" in t or "finalist" in t:
        filters.append(pool_query.Filter(field="award_finalist", op="is_true"))
    if "accredit" in t:
        filters.append(pool_query.Filter(field="regulatory_accreditation", op="is_true"))
    if "ebitda" in t:
        sort_by = "ebitda_est_aud"
    if "evidence" in t:
        sort_by = "s_evidence"
    return pool_query.QuerySpec(filters=filters, sort_by=sort_by, order=order)


def main() -> None:
    store = InMemoryRunStore()
    run_id = "demo"
    store.create_run(run_id)
    store.append_message(run_id, "user", "Founder-owned HVAC installers in Sydney, $1–5M EBITDA")
    store.append_message(run_id, "assistant",
                         "Resolved sector (HVAC / ANZSIC 3234) and geography (NSW / Sydney). "
                         "EBITDA band $1–5M. Ruleset confirmed — searching.")
    for st in (RunStatus.PLANNING, RunStatus.ACQUIRING, RunStatus.RESOLVING,
               RunStatus.ENRICHING, RunStatus.RANKING):
        store.set_status(run_id, st)
    store.update_coverage(run_id, n_raw=420, n_pool=280, n_resolved=96, n_shortlist=12)
    store.set_label(run_id, "HVAC — Sydney demo")

    ranked = []
    for i in range(12):
        rec = _company(i)
        store.save_company(run_id, rec)
        ranked.append(_ranked(rec, i))
    ranked.sort(key=lambda r: r.s_final, reverse=True)
    store.save_shortlist(run_id, ranked)
    store.set_status(run_id, RunStatus.COMPLETE)

    # Keep the NL re-rank fully offline.
    pool_query.parse_query = _deterministic_parse

    manager = RunManager(store, executor=InlineExecutor())
    app = create_app(manager)
    print("Seeded run 'demo' (12 companies). API on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
