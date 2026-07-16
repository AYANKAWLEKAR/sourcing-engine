import json
from pathlib import Path

import pytest

from sourcing.connectors.cache import InMemoryTTLCache
from sourcing.connectors.nata import NATAConnector, normalize_org_name

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nata_sites.json"
SITES = json.loads(_FIX.read_text())


class FakeApifyClient:
    """Mimics the apify-client 3.x surface (Run object with default_dataset_id).

    Records every ``run_input`` passed to ``actor(...).call(run_input=...)`` so
    tests can assert on the page range requested, and returns ``items`` as the
    dataset for every call (single fixed dataset per test).
    """

    def __init__(self, items):
        self._items = items
        self.call_count = 0
        self.run_inputs: list[dict] = []

    def actor(self, actor_id):
        client = self

        class _Actor:
            def call(self, run_input, **kwargs):
                client.call_count += 1
                client.run_inputs.append(run_input)

                class _Run:  # mimic the 3.x Run model attribute
                    default_dataset_id = "ds1"

                return _Run()

        return _Actor()

    def dataset(self, dataset_id):
        client = self

        class _DS:
            def list_items(self):
                class _R:
                    items = client._items

                return _R()

        return _DS()


def test_build_url_encodes_params():
    c = NATAConnector()
    url = c._build_url(state="NSW", search="testing", page=2)
    assert url.startswith("https://nata.com.au/page/2/?")
    assert "post_type=site" in url and "state=NSW" in url and "s=testing" in url
    # NATA's "Active" filter option is the EMPTY value; status=active returns
    # 0 results on the live site (verified in-browser). status= (empty) is correct.
    assert "status=" in url and "status=active" not in url


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


def test_build_input_page_range():
    c = NATAConnector()
    inp = c.build_input({"state": "NSW", "search": "x", "start_page": 2, "pages": 3})
    urls = [u["url"] for u in inp["startUrls"]]
    assert any("/page/2/" in u for u in urls)
    assert any("/page/3/" in u for u in urls)
    assert not any("/page/1/" in u for u in urls)


def test_fetch_sites_warns_on_zero_extracted():
    fake = FakeApifyClient(items=[{"_sentinel": True, "_total_results": 40}])
    c = NATAConnector(cache=InMemoryTTLCache(), client=fake)
    with pytest.warns(UserWarning):
        result = c._fetch_sites({"state": "NSW", "search": "testing"})
    assert result == []


def test_fetch_sites_returns_cards_and_strips_sentinel():
    fake = FakeApifyClient(items=[
        {"_sentinel": True, "_total_results": 1},
        {"parent_org": "Acme Pty Ltd", "site_name": "L", "accreditation_number": "1",
         "site_number": "1", "state": "NSW", "address": "x NSW 2000"},
    ])
    c = NATAConnector(cache=InMemoryTTLCache(), client=fake)
    result = c._fetch_sites({"state": "NSW", "search": "testing"})
    assert len(result) == 1
    row = result[0]
    assert "_sentinel" not in row
    assert row["parent_org"] == "Acme Pty Ltd"
    assert row["service"] == "testing"


# ---------------------------------------------------------------------------
# Task 5: classifier gate -> CompanyRecord (_build_records / fetch / normalize)
# ---------------------------------------------------------------------------

from sourcing.classifiers.ownership_classifier import PRIVATE, Classification  # noqa: E402
from sourcing.models.company import CompanyRecord  # noqa: E402


class _StubClassifier:
    def __init__(self, mapping):
        self._m = mapping  # display name -> category

    def classify(self, names):
        return [Classification(name=n, category=self._m.get(n, "unclear"),
                               confidence=0.9, reasoning="") for n in names]


def test_build_records_keeps_only_private():
    stub = _StubClassifier({"Acme Testing Pty Ltd": PRIVATE,
                            "NSW Health Pathology": "public_sector"})
    c = NATAConnector(classifier=stub)
    recs = c._build_records(SITES)
    names = [r.legal_name for r in recs]
    assert names == ["Acme Testing Pty Ltd"]
    rec = recs[0]
    assert rec.moat_signals.nata_accreditation is True
    assert rec.moat_signals.regulatory_accreditation is True
    assert rec.moat_signals.nata_site_count == 2
    assert rec.moat_signals.nata_multistate is True
    assert rec.location.state == "NSW"  # primary state = highest site count (tie -> first)
    assert rec.abn is None
    assert rec.provenance[0].source == "nata"


def test_build_records_drops_low_confidence_private():
    class _LowConfClassifier:
        def classify(self, names):
            return [Classification(name=n, category=PRIVATE, confidence=0.3, reasoning="")
                    for n in names]

    c = NATAConnector(classifier=_LowConfClassifier())
    with pytest.warns(UserWarning):
        recs = c._build_records(SITES)
    assert recs == []


def test_build_records_classifier_failure_returns_empty():
    class _Boom:
        def classify(self, names):
            raise RuntimeError("ollama down")

    c = NATAConnector(classifier=_Boom())
    with pytest.warns(UserWarning):
        assert c._build_records(SITES) == []


def test_normalize_is_identity():
    rec = CompanyRecord(entity_id="nata:acme testing", legal_name="Acme Testing Pty Ltd")
    c = NATAConnector()
    assert c.normalize(rec) is rec


def test_fetch_wires_fetch_sites_to_records():
    fake = FakeApifyClient(items=[
        {"_sentinel": True, "_total_results": 1},
        {"parent_org": "Acme Testing Pty Ltd", "site_name": "L", "accreditation_number": "2771",
         "site_number": "1", "state": "NSW", "address": "x NSW 2000"},
    ])
    stub = _StubClassifier({"Acme Testing Pty Ltd": PRIVATE})
    c = NATAConnector(client=fake, classifier=stub, cache=InMemoryTTLCache())
    recs = c.fetch({"state": "NSW", "search": "testing"})
    assert len(recs) == 1
    assert isinstance(recs[0], CompanyRecord)
    assert recs[0].legal_name == "Acme Testing Pty Ltd"
