"""Capture one real NATA Apify tile as JSON for integration verification."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sourcing.connectors.nata import NATAConnector


def main() -> None:
    output = Path("NATA-LIVE-SCRAPE-OUTPUT.json")
    try:
        rows = NATAConnector()._fetch_sites({"state": "NSW", "search": "water"})
        output.write_text(json.dumps({
            "executed_at": datetime.now(UTC).isoformat(),
            "source": "nata.com.au via apify/playwright-scraper",
            "params": {"state": "NSW", "search": "water"},
            "raw_site_count": len(rows),
            "raw_sites": rows,
        }, indent=2) + "\n")
    except BaseException as exc:
        output.write_text(json.dumps({"status": "failed", "error": repr(exc)}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
