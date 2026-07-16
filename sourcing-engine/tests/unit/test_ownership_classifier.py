import json

from sourcing.classifiers.ownership_classifier import (
    PRIVATE,
    Classification,
    OwnershipClassifier,
)


def _fake_complete(payload):
    """Return a callable that emits a fixed JSON array of classifications."""

    def _c(prompt: str) -> str:
        return json.dumps(payload)

    return _c


def test_classifies_batch_in_order():
    payload = [
        {"category": "private_commercial", "confidence": 0.95, "reasoning": "Pty Ltd"},
        {"category": "public_sector", "confidence": 0.98, "reasoning": "state health"},
    ]
    clf = OwnershipClassifier(complete=_fake_complete(payload), batch_size=10)
    out = clf.classify(["Acme Labs Pty Ltd", "NSW Health Pathology"])
    assert [c.category for c in out] == [PRIVATE, "public_sector"]
    assert out[0].name == "Acme Labs Pty Ltd"
    assert all(isinstance(c, Classification) for c in out)


def test_low_confidence_marked_but_returned():
    payload = [{"category": "private_commercial", "confidence": 0.4, "reasoning": "guess"}]
    clf = OwnershipClassifier(complete=_fake_complete(payload))
    out = clf.classify(["Ambiguous Name"])
    assert out[0].confidence == 0.4


def test_unparseable_falls_back_to_unclear():
    clf = OwnershipClassifier(complete=lambda p: "not json at all")
    out = clf.classify(["Whatever"])
    assert out[0].category == "unclear"


def test_order_mismatch_falls_back_to_per_item():
    # A 2-item batch that returns only 1 result triggers per-item reclassification.
    calls = {"n": 0}

    def _c(prompt: str) -> str:
        calls["n"] += 1
        # First (batch) call returns the wrong length; per-item calls return 1 each.
        if calls["n"] == 1:
            return json.dumps([{"category": "private_commercial", "confidence": 0.9, "reasoning": "x"}])
        return json.dumps([{"category": "public_sector", "confidence": 0.9, "reasoning": "y"}])

    clf = OwnershipClassifier(complete=_c, batch_size=10)
    out = clf.classify(["A", "B"])
    assert len(out) == 2
    assert calls["n"] >= 3  # 1 failed batch + 2 per-item
