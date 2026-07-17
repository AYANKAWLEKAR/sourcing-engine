"""NATA orchestration tests (Task 7): params-tiling branch + registry entry."""
from __future__ import annotations

from sourcing.orchestrator import params_for_connector
from sourcing.rank.buybox import BuyBox


def test_nata_params_tile_state_x_keyword():
    bb = BuyBox(thesis="testing labs", sector_keywords=["testing", "calibration"],
                states=["NSW", "VIC"])
    tiles = params_for_connector("nata_accreditation", bb)
    combos = {(t["state"], t["search"]) for t in tiles}
    assert ("NSW", "testing") in combos
    assert ("VIC", "calibration") in combos
    # NATA's "Active" filter is the EMPTY value on the live site — status="active"
    # returns 0 results for every query (verified in-browser, Task 9).
    assert all(t["status"] == "" for t in tiles)


def test_nata_registered_as_scrape_connector():
    from sourcing.rag.registry_seed import load_seed_registry

    entry = {e.source_id: e for e in load_seed_registry()}["nata_accreditation"]
    assert entry.connector_type.value == "scrape"
    assert entry.connector_ref.endswith("nata.NATAConnector")
    assert entry.enabled is True


def test_nata_uses_domcontentloaded_for_js_rendered_results():
    from sourcing.connectors.nata import NATAConnector

    assert NATAConnector().build_input({"state": "NSW", "search": "water"})["waitUntil"] == (
        "domcontentloaded"
    )
