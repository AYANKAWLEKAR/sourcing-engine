"""LinkedInHeadcountConnector — headcount enrichment (plan §5.2, SHORTLIST-GATED).

Apify ``apt_marble/linkedin-company-employees-scraper``. ``gate="shortlist_only"``:
this must NOT run during the full-pool discovery sweep — only on a ranked shortlist.
Cache TTL 30 days.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base_scrape import ScrapeConnector

if TYPE_CHECKING:
    from ..models.company import CompanyRecord

SOURCE_ID = "linkedin_headcount"  # matches the registry source_id


class LinkedInHeadcountConnector(ScrapeConnector):
    source_id: str = SOURCE_ID
    actor_id: str = "apt_marble/linkedin-company-employees-scraper"
    cache_ttl_seconds: int = 30 * 24 * 3600
    gate: str = "shortlist_only"  # must not run in the full-pool sweep

    def build_input(self, params: dict) -> dict:
        out: dict = {"maxResults": 1}
        if params.get("companyUrl"):
            out["companyUrl"] = params["companyUrl"]
        elif params.get("companyName"):
            out["companyName"] = params["companyName"]
        return out

    def normalize(self, raw: dict) -> CompanyRecord:
        from ..models.company import CompanyRecord, Provenance, Size

        count = raw.get("employeeCount") or raw.get("employees")
        name = raw.get("companyName") or raw.get("name")
        return CompanyRecord(
            entity_id=f"linkedin:{raw.get('companyUrl') or name}",
            abn=None,
            legal_name=name,
            country="Australia",
            size=Size(employee_count=count, employee_source="linkedin"),
            provenance=[Provenance(field="employee_count", source=SOURCE_ID, confidence=0.70)],
        )
