import json
from pathlib import Path

from sourcing.connectors.nata import NATAConnector, normalize_org_name

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nata_sites.json"
SITES = json.loads(_FIX.read_text())


def test_build_url_encodes_params():
    c = NATAConnector()
    url = c._build_url(state="NSW", search="testing", page=2)
    assert url.startswith("https://nata.com.au/page/2/?")
    assert "post_type=site" in url and "state=NSW" in url and "s=testing" in url
    assert "status=active" in url


def test_normalize_org_name_strips_suffix_and_case():
    assert normalize_org_name("Acme Testing Pty Ltd") == normalize_org_name("Acme Testing Pty. Ltd.")
    assert normalize_org_name("ACME  Testing") == "acme testing"


def test_group_by_parent_aggregates_multisite():
    c = NATAConnector()
    parents = c._group_by_parent(SITES)
    by_name = {p["parent_org"]: p for p in parents}
    acme = by_name["Acme Testing Pty Ltd"]  # first-seen display name wins
    assert acme["site_count"] == 2
    assert set(acme["states"]) == {"NSW", "VIC"}
    assert acme["accreditation_numbers"] == ["2771"]  # deduped
    assert "testing" in acme["service_types"]
    assert len(parents) == 2  # two distinct parents
