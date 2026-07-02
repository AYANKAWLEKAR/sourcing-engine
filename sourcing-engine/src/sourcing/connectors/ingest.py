"""Ingest helpers: persist normalised CompanyRecords into the companies table."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models.company import CompanyRecord


def upsert_companies(session: Session, records: list[CompanyRecord]) -> int:
    """Upsert a batch of CompanyRecords into the ``companies`` table.

    Returns the number of rows written.  Uses ON CONFLICT (entity_id) DO UPDATE
    so re-running an ingest is safe (idempotent for the same entity_id).
    """
    import json

    inserted = 0
    for rec in records:
        session.execute(
            text(
                "INSERT INTO companies (entity_id, abn, acn, legal_name, record) "
                "VALUES (:entity_id, :abn, :acn, :legal_name, :record) "
                "ON CONFLICT (entity_id) DO UPDATE SET "
                "  abn         = EXCLUDED.abn, "
                "  acn         = EXCLUDED.acn, "
                "  legal_name  = EXCLUDED.legal_name, "
                "  record      = EXCLUDED.record"
            ),
            {
                "entity_id": rec.entity_id,
                "abn": rec.abn,
                "acn": rec.acn,
                "legal_name": rec.legal_name,
                "record": json.dumps(rec.model_dump()),
            },
        )
        inserted += 1
    session.commit()
    return inserted


def load_companies(session: Session, limit: int = 100, state: str | None = None) -> list[CompanyRecord]:
    """Load CompanyRecords from the database, optionally filtered by state."""
    import json

    if state:
        rows = session.execute(
            text(
                "SELECT record FROM companies "
                "WHERE record->>'location' IS NOT NULL "
                "AND record->'location'->>'state' = :state "
                "LIMIT :limit"
            ),
            {"state": state, "limit": limit},
        ).fetchall()
    else:
        rows = session.execute(
            text("SELECT record FROM companies LIMIT :limit"),
            {"limit": limit},
        ).fetchall()

    return [CompanyRecord(**json.loads(row[0])) for row in rows if row[0]]
