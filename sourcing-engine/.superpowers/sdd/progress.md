# NATA Connector — SDD Progress Ledger

Branch: web-scraping
Plan: docs/superpowers/plans/2026-07-15-nata-accreditation-connector.md

## Cross-task corrections (controller decisions)
- Tasks 4/5: `fetch()` does pages→group→batch-classify→returns list[CompanyRecord];
  `normalize(record)` is an IDENTITY pass-through. Reason: orchestrator.fetch_all calls
  `connector.normalize(r) for r in raws` per-item, so normalize must return one record.
  This preserves batch classification and touches no engine code.

## Task status
(none complete yet)
