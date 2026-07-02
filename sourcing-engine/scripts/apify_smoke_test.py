"""Standalone Apify check (plan §4). Runs the Google Maps actor on a tiny query.

Run:  python scripts/apify_smoke_test.py
Pass: prints >=1 place with a title and a location.

Deliberately isolated — depends only on apify-client, so any failure here is
unambiguously about Apify (token/billing/actor), not about connector code.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_token() -> str | None:
    token = os.environ.get("APIFY_API_TOKEN")
    if token:
        return token
    # Fallback: parse the sibling .env so the script is self-contained.
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("APIFY_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None


def main() -> None:
    token = _load_token()
    if not token:
        sys.exit("FAIL: APIFY_API_TOKEN not set in environment/.env")

    from apify_client import ApifyClient

    client = ApifyClient(token)
    run_input = {
        "searchStringsArray": ["HVAC installer"],
        "locationQuery": "Brisbane QLD Australia",
        "maxCrawledPlacesPerSearch": 3,  # keep tiny — this is a smoke test
        "language": "en",
        "countryCode": "au",  # actor requires lowercase ISO-3166
        "scrapeContacts": False,
    }

    print("Running compass/crawler-google-places (max 3 places)…")
    run = client.actor("compass/crawler-google-places").call(run_input=run_input)
    # apify-client 3.x returns a typed Run model (attribute), not a dict.
    dataset_id = getattr(run, "default_dataset_id", None) or run["defaultDatasetId"]
    items = client.dataset(dataset_id).list_items().items

    print(f"Returned {len(items)} places.")
    if not items:
        sys.exit("FAIL: actor returned 0 items — check token, billing, or actor availability")

    p = items[0]
    print("Sample keys:", sorted(p.keys())[:12])
    print("title:", p.get("title"))
    print("address:", p.get("address"))
    print("website:", p.get("website"))
    print("location:", p.get("location"))
    print("categories:", p.get("categories"))
    print("phone:", p.get("phone"))
    print("reviewsCount:", p.get("reviewsCount"))

    # Persist one real item as the offline unit-test fixture (plan §5.3 guardrail).
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "maps_place.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(json.dumps(p, indent=2, default=str))
    print(f"Saved fixture: {fixture}")

    print("PASS: Apify works and returns usable place records.")


if __name__ == "__main__":
    main()
