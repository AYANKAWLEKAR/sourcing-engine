# Three New Bulk Connectors — ABN Bulk Extract, IPGOD, ASX Listed

Built 2026-07-05. All three subclass `BulkConnector` (download once → DuckDB → local SQL),
following the `asic_bulk.py` pattern. This doc covers what landed, what was verified, and
**what must be done manually**.

## What was built

| Connector | Module | Registry id / table | Fills |
|---|---|---|---|
| ABN Bulk Extract | `connectors/abn_bulk.py` | `abn_bulk_extract` / `abn_extract` | Spine for sole traders / partnerships / trusts (valid ABN, no ASIC row) |
| IPGOD (IP Australia) | `connectors/ipgod.py` | `ipgod` / `ipgod` | `moat_signals.ip` / `ip_count` / `ip_types` |
| ASX Listed | `connectors/asx_listed.py` | `asx_listed_list` / `asx_listed` | `ownership.listed_entity` (makes the `listed_entity` EXCLUDE fire) |

Supporting changes: `MoatSignals` gained `ip_count` + `ip_types`; five new settings in
`config.py`; `EnrichmentNode` runs ASX + IPGOD as free local lookups (step 0, before
AusTender); `EntityResolver` falls back to the ABR bulk extract when the ASIC lookup misses;
`orchestrator.py` treats IPGOD/ASX as enrichment sources; registry entries corrected (they
had stale `connector_ref`s and a bogus `anzsic_code` claim on the ABR extract — the public
extract carries no ANZSIC codes).

## Wiring semantics (important)

- **Set-on-match only.** ASX/IPGOD write a signal only on a positive match. A miss leaves the
  field `None` (unknown), never `False` — so screening never excludes on absence from a
  name-only roster or on partial IPGOD coverage. Misses are recorded as flags
  (`ipgod_checked_no_ip`, `asx_name_match_only`).
- **No surprise downloads.** The ABR fallback in `EntityResolver` only wires when
  `ABN_BULK_ENABLED=true`. IPGOD/ASX defaults wire only when their data resolves. Constructing
  the pipeline never demands bulk files be present.

## Verification

- **Offline unit suite**: 343 passing, ruff clean. 76 new unit tests across
  `test_abn_bulk_connector.py`, `test_ipgod_connector.py`, `test_asx_connector.py`, plus
  extensions to `test_enrichment.py`, `test_entity_resolution.py`, `test_models.py`,
  `test_registry.py`. All synthetic — no network, no credits.
- **Live integration** (data downloaded into `data/`): ASX + IPGOD slices pass (10 tests).
  IPGOD loaded **245,289 applicant ABNs** (36,844 patent, 208,445 trademark) in 56s. ASX
  loaded **1,831 listings**; `1414 Degrees Limited` → `(True, 0.75)`. ABN bulk extract
  (~20M records across both zips) loads via `ensure_loaded()`.

Run them:
```bash
cd sourcing-engine && source .venv/bin/activate
pytest -m "not integration"                                    # 343 offline
pytest tests/integration/test_asx_listed.py \
       tests/integration/test_ipgod.py \
       tests/integration/test_abn_bulk.py -m integration -v    # live (skips if data absent)
```

## MANUAL STEPS

### ASX — done, refresh occasionally
The directory CSV is already in `data/ASX_Listed_Companies_06-07-2026_06-39-47_AEST.csv`
(1,831 rows). The connector globs the newest `ASX_Listed_Companies_*.csv`. To refresh: export
the company directory from asx.com.au every few months and drop it in `data/`. If a future
export includes an ACN column, match confidence auto-upgrades from 0.75 (name) to 0.95 (ACN)
— no code change.

### ABN Bulk Extract — downloaded this run; refresh monthly
The two zips (`public_split_1_10.zip`, `public_split_11_20.zip`, ~940MB) are in
`data/abn_bulk/` (gitignored). `.env` has `ABN_BULK_ENABLED=true`, `ABN_BULK_DIR=data/abn_bulk`.
- **Disk**: ~940MB zips + the `abn_extract` table in `data/bulk.duckdb` (grows the shared DB
  by ~2–3GB). Nothing is unpacked to disk — XML streams out of the zips.
- **Refresh**: ABR republishes weekly; a monthly `connector.ensure_loaded(force=True)` is
  plenty. To let the connector self-download instead of manual placement, set
  `ABN_BULK_DOWNLOAD=true` (it CKAN-resolves `abn-bulk-extract` and streams both zips).

### IPGOD — downloaded this run; refresh annually
Two applicant CSVs from the **IPGOD2022** dataset are in `data/ipgod/`
(`patent-party-activity.csv`, `trade-mark-party-activity.csv`, ~1.3GB total). `.env` points
`IPGOD_CSV_PATHS` at them. IPGOD releases yearly — to update, download the newer party-activity
tables from data.gov.au and repoint `IPGOD_CSV_PATHS`. To add designs / plant-breeder's rights,
append their party-activity CSV paths (ip_type is inferred from the filename).

### Not done (deliberate, per the build report)
- `pe_vc_backed` remains unfilled — only Inven (paid MCP) feeds it; you asked to leave it.
- Award registers (Local Business, Trades Champion), AusTender bulk OCDS, ABS CABEE, Inven MCP
  remain on the build list (report §3, items 4–8).
