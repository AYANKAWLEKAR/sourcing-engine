# GrantConnect Integration Report

**Date:** 2026-07-16  
**Status:** implemented and verified; production bulk refresh remains manually staged pending a downloadable official export.

## Pipeline fit

GrantConnect is wired as an **enrichment-only** `BulkConnector`. It is included in the source registry but excluded from the discovery sweep, so it cannot pollute a buy-box candidate pool with universities, government bodies, or unrelated grant recipients. When `GRANTCONNECT_ENABLED=true`, `PipelineComponents.build_default()` supplies the connector to `EnrichmentNode` after ABN resolution.

The connector uses the same shared DuckDB database as other bulk sources, synthesizes a stable award id, and indexes `recipient_abn`. It writes these separate `MoatSignals` fields:

- `gov_investment`, `gov_grants_total_aud`, `gov_grants_count`
- `gov_grant_programs`, `gov_granting_agencies`, `gov_grants_most_recent`

They are intentionally not merged with AusTender's `gov_contract*` procurement fields. A grants refresh is applied even on an ABN enrichment-cache hit, avoiding a stale 14-day record cache hiding newer local grant data.

## Ranking and LLM fit

The locked statistical-fit formula is unchanged. Grants contribute only to the deterministic evidence layer and the Claude judge context:

- Procurement and grant investment each have independently log-scaled government evidence.
- The latest grant date supplies a gentle recency discount (full credit through 3 years; declines to a 40% floor by 10 years).
- Their probabilistic union rewards companies with both government procurement and government investment without counting either dollar amount as the other type.
- The deterministic shortlist chip is e.g. `$1,250,000 Commonwealth grants`; Claude receives a distinct `gov_grants` summary with count and program names.

## Source-access finding

The plan's proposed initial data.gov.au source exists: the Department of Industry, Science and Resources Grant Awards dataset is present in CKAN. However, on 2026-07-16 its resource labelled `CSV` resolves to the protected GrantConnect **Awards by agency** help page, not a downloadable CSV. A live attempt returned HTTP 403.

The default source entry is therefore marked `access: manual`. Before enabling `GRANTCONNECT_ENABLED`, stage an authorised CSV and add `file: path/to/awards.csv` under that source (or add a future verified direct-CSV source). The connector fails with this explicit instruction instead of silently producing an empty table.

## Test results

| Coverage | Command | Result |
|---|---|---|
| Connector parsing, deduplication, aggregation, enrichment, ranking, registry | focused unit suite | `50 passed` |
| Full offline suite | `pytest -m 'not integration' -q` | `466 passed, 58 deselected` (one pre-existing FastAPI deprecation warning) |
| Python lint | `ruff check` on changed Python and test files | passed |
| Frontend lint | `npm run lint` in `frontend/` | passed |
| Online source integration | `pytest tests/integration/test_grantconnect_live.py -q` | `1 passed` against the live data.gov.au CKAN metadata endpoint |
| Online Claude integration | `pytest tests/integration/test_grantconnect_claude.py -q` | `1 passed` using one short Claude Haiku judge request |
| Synthetic full run (multi-turn) | `pytest tests/unit/test_grantconnect_multiturn_run.py -q` | `1 passed`: buy-box → planning → acquiring → resolving → enriching → ranking → complete; grant signal persisted on shortlist |

## Demo result

`python scripts/grantconnect_demo.py` stages one representative Industry award, enriches an ABN-resolved B2B advanced-manufacturing company, then ranks it through the normal evidence and judge path:

```json
{
  "rows_loaded": 1,
  "gov_investment": true,
  "gov_grants_total_aud": 1250000,
  "gov_grant_programs": ["Modern Manufacturing Initiative"],
  "s_evidence": 0.273,
  "s_final": 0.6508,
  "standout_signals": ["$1,250,000 Commonwealth grants"]
}
```

## Operational enablement

1. Obtain and stage an authorised official GrantConnect award CSV.
2. Add its local path as `file:` in `data/grantconnect_sources.yaml`.
3. Run `GrantConnectBulkConnector(...).ensure_loaded(force=True)` on the refresh cadence.
4. Set `GRANTCONNECT_ENABLED=true` for sourcing runs.

Plan B's Playwright delta was not implemented: it is not needed for the bulk MVP, and the current official bulk export needs a verifiable download path first. It should be added only after a stable, permitted live search workflow is confirmed.
