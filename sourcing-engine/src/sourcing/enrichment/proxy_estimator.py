"""ProxyEstimator — rough revenue/EBITDA estimate for the PROXY_GATE flag only.

``revenue_est = employee_count × rev_per_employee(anzsic)``;
``ebitda_est  = revenue_est × ebitda_margin(anzsic)``, from a static ATO-style
benchmark table. Confidence is capped low (≤ 0.4): this exists to set the
PROXY_GATE band on ``ebitda_aud``, it does NOT feed the statistical score (plan §2.2).

Employee count comes from the shortlist-gated LinkedIn connector, so the proxy
runs late (on gate-passing records), not in the full sweep.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import DATA_DIR

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

MAX_CONFIDENCE = 0.4


class ProxyEstimator:
    def __init__(self, benchmarks_path: Path | None = None):
        self._table = _load_benchmarks(benchmarks_path or DATA_DIR / "ato_benchmarks.csv")

    def estimate(self, record: CompanyRecord) -> CompanyRecord:
        from ..models.company import Provenance

        emp = record.size.employee_count
        if not emp or emp <= 0:
            record.flags.append("unverified:ebitda_aud:no_employee_count")
            return record

        rev_per_emp, margin = self._lookup(record.sector.anzsic)
        revenue = emp * rev_per_emp
        ebitda = revenue * margin
        # Confidence scales with ANZSIC certainty but is hard-capped low.
        conf = min(MAX_CONFIDENCE, 0.25 + 0.15 * (record.sector.anzsic_confidence or 0.0))

        record.size.revenue_est_aud = round(revenue, 2)
        record.size.revenue_confidence = conf
        record.size.ebitda_est_aud = round(ebitda, 2)
        record.size.ebitda_confidence = conf
        record.provenance.append(
            Provenance(field="ebitda_aud", source="proxy_estimator",
                       locator="employee_count × ANZSIC benchmark", confidence=conf)
        )
        return record

    def _lookup(self, anzsic: list[str]) -> tuple[float, float]:
        code = (anzsic[0] if anzsic else "")[:2]
        if code in self._table:
            return self._table[code]
        return self._table["DEFAULT"]


def _load_benchmarks(path: Path) -> dict[str, tuple[float, float]]:
    table: dict[str, tuple[float, float]] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            table[row["anzsic_prefix"]] = (
                float(row["rev_per_employee_aud"]),
                float(row["ebitda_margin"]),
            )
    table.setdefault("DEFAULT", (200000.0, 0.12))
    return table
