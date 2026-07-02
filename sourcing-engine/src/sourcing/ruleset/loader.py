"""Origo CSV ruleset loader (plan §5.4).

Parses ``data/origo_filter_spec.csv`` into a base :class:`FilterRuleset`:
  * parses the human-readable ``logic`` cell into a structured dict,
  * drops ``professional_licence_required`` ("delete this"),
  * derives each rule's ``discovery_action`` via :func:`derive_discovery_action`,
  * assigns default weights to SCORE rules.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..config import DATA_DIR
from ..models.filter_rule import DiscoveryAction, FilterRule, FilterRuleset, ScreenTier
from .derive import derive_discovery_action

# Fields removed from the spec ("delete this").
DROPPED_FIELDS: frozenset[str] = frozenset({"professional_licence_required"})

BASE_VERSION = "origo_csv_v130626"

# Default SCORE weights derived from screen tiers (spec §7.4). Applied to any
# rule whose derived action is SCORE.
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "anzsic_code": 0.15,
    "sector_keyword_match": 0.15,
    "ai_disruption_risk": 0.15,
    "market_fragmentation": 0.12,
    "state": 0.10,
    "business_model": 0.08,
}
FALLBACK_SCORE_WEIGHT = 0.05


def _parse_number(token: str) -> float | int:
    """Parse a numeric token, tolerating thousands separators."""
    cleaned = token.strip().replace(",", "")
    value = float(cleaned)
    return int(value) if value.is_integer() else value


def _parse_bool(token: str) -> bool:
    return token.strip().lower() in {"true", "yes", "1"}


def parse_logic(filter_type: str, raw: str) -> dict:
    """Parse a ``logic`` cell into a structured dict keyed by filter_type.

    Examples
    --------
    range:     ``MIN: 1,000,000 | MAX: 15,000,000 | SWEET_SPOT: 1,500,000 - 10,000,000``
               -> ``{"min": 1000000, "max": 15000000, "sweet_spot": [1500000, 10000000]}``
    threshold: ``MIN: 5`` -> ``{"min": 5}``  /  ``MAX: 30`` -> ``{"max": 30}``
    match/whitelist: ``VALUES: NSW, VIC`` -> ``{"values": ["NSW", "VIC"]}``
    keyword:   ``INCLUDE: a, b | EXCLUDE: c`` -> ``{"include": ["a","b"], "exclude": ["c"]}``
    boolean:   ``EQUALS: false`` -> ``{"equals": False}``
    """
    raw = (raw or "").strip()
    if not raw:
        return {}

    segments = [seg.strip() for seg in raw.split("|") if seg.strip()]
    out: dict = {}
    for seg in segments:
        if ":" not in seg:
            continue
        key, value = seg.split(":", 1)
        key = key.strip().upper()
        value = value.strip()

        if key in {"MIN", "MAX"}:
            out[key.lower()] = _parse_number(value)
        elif key == "SWEET_SPOT":
            lo, hi = (p for p in value.split("-"))
            out["sweet_spot"] = [_parse_number(lo), _parse_number(hi)]
        elif key in {"VALUES", "INCLUDE", "EXCLUDE"}:
            items = [v.strip() for v in value.split(",") if v.strip()]
            out[key.lower() if key != "VALUES" else "values"] = items
        elif key == "EQUALS":
            out["equals"] = _parse_bool(value)
        else:
            out[key.lower()] = value
    return out


def _build_rule(row: dict) -> FilterRule:
    tier = ScreenTier(row["screen_tier"].strip().upper())
    scrapeable = _parse_bool(row["scrapeable"])
    proxyable = _parse_bool(row["proxyable"])
    filter_type = row["filter_type"].strip()

    action = derive_discovery_action(tier, scrapeable, proxyable, filter_type)
    weight = None
    if action == DiscoveryAction.SCORE:
        weight = DEFAULT_SCORE_WEIGHTS.get(row["field"].strip(), FALLBACK_SCORE_WEIGHT)

    sources = [s.strip() for s in (row.get("sources") or "").split(";") if s.strip()]

    return FilterRule(
        field=row["field"].strip(),
        group=row["group"].strip(),
        data_type=row["data_type"].strip(),
        filter_type=filter_type,
        screen_tier=tier,
        logic=parse_logic(filter_type, row.get("logic", "")),
        sources=sources,
        scrapeable=scrapeable,
        proxyable=proxyable,
        discovery_action=action,
        weight=weight,
        notes=(row.get("notes") or "").strip() or None,
    )


def load_rules(csv_path: Path | None = None) -> list[FilterRule]:
    path = csv_path or (DATA_DIR / "origo_filter_spec.csv")
    rules: list[FilterRule] = []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            field = row["field"].strip()
            if field in DROPPED_FIELDS:
                continue
            rules.append(_build_rule(row))
    return rules


def load_origo_ruleset(
    csv_path: Path | None = None,
    ruleset_id: str = "origo-base",
    name: str = "Origo default — TIC services, SE-AU",
) -> FilterRuleset:
    """Load the Origo CSV into an unconfirmed base :class:`FilterRuleset`."""
    rules = load_rules(csv_path)
    ranking_weights = {
        r.field: r.weight for r in rules if r.discovery_action == DiscoveryAction.SCORE
    }
    return FilterRuleset(
        ruleset_id=ruleset_id,
        name=name,
        base_version=BASE_VERSION,
        rules=rules,
        ranking_weights=ranking_weights,
        confirmed=False,
    )
