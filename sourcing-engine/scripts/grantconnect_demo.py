"""Offline GrantConnect pipeline demo: staged CSV -> enrichment -> ranked evidence."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sourcing.connectors.grantconnect import GrantConnectBulkConnector
from sourcing.llm import LLMResponse
from sourcing.models.company import CompanyRecord, Location, Provenance
from sourcing.rank.buybox import BuyBox
from sourcing.rank.judge import LLMJudge
from sourcing.rank.rank import rank_pool


class DemoLLM:
    """Deterministic judge transport for a no-network reproducible demo."""

    def chat(self, *args, **kwargs):
        return LLMResponse(
            text=json.dumps(
                {"fit": 0.85, "rationale": "advanced manufacturing with verified grant evidence", "standout_signals": []}
            )
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="grantconnect-demo-") as tmp:
        root = Path(tmp)
        (root / "awards.csv").write_text(
            "Recipient Name,Recipient ABN,Grant Program,Grant Award Value,Grant Award Date,Recipient State\n"
            "Acme Robotics Pty Ltd,12345678901,Modern Manufacturing Initiative,1250000,15/06/2025,QLD\n",
            encoding="latin-1",
        )
        (root / "sources.yaml").write_text(
            "sources:\n"
            "  - source_dataset: demo-industry\n"
            "    granting_agency: Department of Industry, Science and Resources\n"
            "    file: awards.csv\n",
            encoding="utf-8",
        )
        connector = GrantConnectBulkConnector(
            db_path=root / "bulk.duckdb", sources_path=root / "sources.yaml", raw_dir=root / "raw"
        )
        try:
            connector.ensure_loaded()
            record = CompanyRecord(
                entity_id="abn:12345678901",
                abn="12345678901",
                legal_name="Acme Robotics Pty Ltd",
                location=Location(state="QLD", postcode="4000"),
                business_model="B2B",
            )
            record.sector.category_text = ["advanced manufacturing"]
            record.sector.keyword_hits = ["advanced manufacturing"]
            record.sector.keyword_density = 1.0
            record.provenance.append(Provenance(field="abn", source="demo", confidence=1.0))
            connector.enrich_record(record)
            buybox = BuyBox(
                thesis="Australian advanced-manufacturing SMEs",
                sector_keywords=["advanced manufacturing"],
                states=["QLD"],
                target_models=["B2B"],
            )
            ranked = rank_pool(
                [record], buybox, judge=LLMJudge(llm=DemoLLM(), model="demo"), top_k=1, judge_k=1
            )[0]
            print(
                json.dumps(
                    {
                        "rows_loaded": connector.row_count(),
                        "gov_investment": record.moat_signals.gov_investment,
                        "gov_grants_total_aud": record.moat_signals.gov_grants_total_aud,
                        "gov_grant_programs": record.moat_signals.gov_grant_programs,
                        "s_evidence": ranked.s_evidence,
                        "s_final": ranked.s_final,
                        "standout_signals": ranked.standout_signals,
                    },
                    indent=2,
                    default=str,
                )
            )
        finally:
            connector.close()


if __name__ == "__main__":
    main()
