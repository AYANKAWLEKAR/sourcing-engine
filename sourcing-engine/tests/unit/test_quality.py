"""Operating-entity / shell detection (W1) — demotes holding shells softly."""
from __future__ import annotations

from sourcing.models.company import CompanyRecord, Location, Ownership, Sector
from sourcing.rank.buybox import BuyBox
from sourcing.rank.quality import (
    OPERATING_ENTITY_FLAG,
    flag_operating_entity,
    is_non_operating,
)
from sourcing.rank.score import statistical_fit
from sourcing.rank.screen import screen

_BB = BuyBox(thesis="HVAC", sector_keywords=["hvac"], states=["QLD"], target_models=["B2B"])


def _co(name, *, structure=None, keyword_hits=None, cats=None, state="QLD"):
    return CompanyRecord(
        entity_id=f"x:{name}", abn="1" * 11, legal_name=name,
        location=Location(state=state),
        ownership=Ownership(structure_guess=structure),
        sector=Sector(keyword_hits=keyword_hits or [], category_text=cats or []),
        business_model="B2B",
    )


class TestIsNonOperating:
    def test_shell_name_no_operating_evidence_flagged(self):
        assert is_non_operating(_co("BENGUERRA INVESTMENTS PTY LTD", cats=["Air conditioning contractor"]))
        assert is_non_operating(_co("WILLANE INVESTMENTS PTY LTD"))
        assert is_non_operating(_co("Smith Family Holdings Pty Ltd"))

    def test_shell_name_with_website_keywords_cleared(self):
        # Real operating business that happens to have "Holdings" in the name AND
        # website keyword evidence → not flagged.
        assert not is_non_operating(_co("Cool Holdings Pty Ltd", keyword_hits=["hvac", "air conditioning"]))

    def test_trust_structure_flagged(self):
        assert is_non_operating(_co("The Young Family Trust", structure="trust"))
        assert is_non_operating(_co("Some Council", structure="government"))

    def test_ordinary_operating_names_not_flagged(self):
        assert not is_non_operating(_co("Neptune Refrigeration & Air Conditioning"))
        assert not is_non_operating(_co("Cold Phase Air Conditioning"))
        # "Distributors" / "Group" are operating businesses — the judge handles model fit.
        assert not is_non_operating(_co("ZENITH DISTRIBUTORS PTY LTD"))
        assert not is_non_operating(_co("Acme Group Pty Ltd"))


class TestFlagOperatingEntity:
    def test_appends_flag_on_shell(self):
        rec = _co("BENGUERRA INVESTMENTS PTY LTD")
        assert flag_operating_entity(rec) is True
        assert OPERATING_ENTITY_FLAG in rec.flags

    def test_idempotent(self):
        rec = _co("BENGUERRA INVESTMENTS PTY LTD")
        flag_operating_entity(rec)
        flag_operating_entity(rec)
        assert rec.flags.count(OPERATING_ENTITY_FLAG) == 1

    def test_no_flag_on_operating(self):
        rec = _co("Neptune Refrigeration")
        assert flag_operating_entity(rec) is False
        assert OPERATING_ENTITY_FLAG not in rec.flags


class TestScreenWiring:
    def test_shell_survives_but_flagged(self):
        rec = _co("BENGUERRA INVESTMENTS PTY LTD")
        assert screen(rec, _BB) is True                 # soft — not excluded
        assert rec.screen.status == "survivor"
        assert OPERATING_ENTITY_FLAG in rec.flags

    def test_operating_entity_flag_demotes_score(self):
        # Same record, with vs without the flag → the unverified penalty lowers score.
        shell = _co("BENGUERRA INVESTMENTS PTY LTD", cats=["Air conditioning contractor"])
        clean = _co("Neptune Refrigeration", cats=["Air conditioning contractor"])
        screen(shell, _BB)
        screen(clean, _BB)
        assert OPERATING_ENTITY_FLAG in shell.flags
        assert OPERATING_ENTITY_FLAG not in clean.flags
        assert statistical_fit(shell, _BB) < statistical_fit(clean, _BB)


# ---------------------------------------------------------------------------
# W4 — judge summary gets age/size/operating-entity context
# ---------------------------------------------------------------------------

def test_judge_summary_includes_operating_warning_and_context():
    from sourcing.models.company import Size
    from sourcing.rank.judge import summarize
    from sourcing.rank.quality import OPERATING_ENTITY_FLAG

    rec = _co("BENGUERRA INVESTMENTS PTY LTD", structure="private-company")
    rec.flags.append(OPERATING_ENTITY_FLAG)
    rec.size = Size(employee_count=8, revenue_est_aud=2_000_000, revenue_confidence=0.9)
    rec.ownership.pe_vc_backed = True
    s = summarize(rec)
    assert "HOLDING/INVESTMENT" in s
    assert "entity_structure: private-company" in s
    assert "employees: 8" in s
    assert "revenue_aud" in s and "direct" in s   # high-confidence → direct
    assert "pe_vc_backed: YES" in s


def test_judge_summary_operating_business_no_warning():
    from sourcing.rank.judge import summarize

    s = summarize(_co("Neptune Refrigeration"))
    assert "HOLDING/INVESTMENT" not in s


# ---------------------------------------------------------------------------
# W5 — quality-eval metrics
# ---------------------------------------------------------------------------

def test_shortlist_quality_metrics():
    from sourcing.models.ranking import RankedCompany
    from sourcing.rank.quality import shortlist_quality_metrics

    shell = _co("Shell Investments Pty Ltd")
    shell.flags.append(OPERATING_ENTITY_FLAG)
    good = _co("Neptune Refrigeration", keyword_hits=["hvac"])
    third = _co("Cold Phase", keyword_hits=["hvac"])
    resolved = [shell, good, third]  # 2/3 have sector signal; all pe_vc None
    shortlist = [
        RankedCompany(record=shell, s_stat=50.0, s_final=0.40, judge_fit=0.30),
        RankedCompany(record=good, s_stat=60.0, s_final=0.70, judge_fit=0.80),
    ]
    m = shortlist_quality_metrics(shortlist, resolved)
    assert m["shortlist_size"] == 2
    assert m["shell_rate_topN"] == 0.5
    assert m["mean_judge_fit"] == 0.55
    assert m["sector_signal_coverage"] == round(2 / 3, 3)
    assert m["pe_vc_unknown_rate"] == 1.0
