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


def test_extract_handles_index_keyed_object():
    # qwen2.5:3b sometimes returns a dict keyed by index instead of a JSON array.
    payload = {
        "[0]": {"category": "private_commercial", "confidence": 0.9, "reasoning": "x"},
        "[1]": {"category": "public_sector", "confidence": 0.9, "reasoning": "y"},
    }

    def _c(prompt: str) -> str:
        return json.dumps(payload)

    clf = OwnershipClassifier(complete=_c, batch_size=10)
    out = clf.classify(["A", "B"])
    assert [c.category for c in out] == [PRIVATE, "public_sector"]


def test_extract_handles_categories_envelope():
    # qwen sometimes wraps the array as {"categories": [...]}.
    payload = {
        "categories": [
            {"category": "private_commercial", "confidence": 0.9, "reasoning": "x"},
            {"category": "public_sector", "confidence": 0.9, "reasoning": "y"},
        ]
    }

    def _c(prompt: str) -> str:
        return json.dumps(payload)

    clf = OwnershipClassifier(complete=_c, batch_size=10)
    out = clf.classify(["A", "B"])
    assert [c.category for c in out] == [PRIVATE, "public_sector"]


def test_extract_handles_single_object():
    # For a 1-item batch, qwen sometimes returns a single object instead of a 1-element array.
    payload = {"category": "private_commercial", "confidence": 0.8, "reasoning": "x"}

    def _c(prompt: str) -> str:
        return json.dumps(payload)

    clf = OwnershipClassifier(complete=_c)
    out = clf.classify(["Solo"])
    assert len(out) == 1
    assert out[0].category == PRIVATE


def test_extract_handles_nested_single_element_list():
    # qwen2.5:3b intermittently returns a nested [[{...}]] on the single-item
    # (per-item reclassification) path. Observed live in test_classifier_live_qwen:
    # the inner list reached _to_classification and crashed with
    # AttributeError: 'list' object has no attribute 'get'. It must unwrap cleanly.
    payload = [[{"category": "private_commercial", "confidence": 1.0, "reasoning": "x"}]]

    def _c(prompt: str) -> str:
        return json.dumps(payload)

    clf = OwnershipClassifier(complete=_c)
    out = clf.classify(["Solo"])
    assert len(out) == 1
    assert out[0].category == PRIVATE
    assert out[0].confidence == 1.0


def test_malformed_element_degrades_to_unclear_not_crash():
    # A batch whose elements are not dicts (bare string / number) must not raise —
    # each malformed element degrades to "unclear" so one bad item can't kill the batch.
    def _c(prompt: str) -> str:
        return json.dumps(["not a dict", 42])

    clf = OwnershipClassifier(complete=_c, batch_size=10)
    out = clf.classify(["A", "B"])
    assert len(out) == 2
    assert all(c.category == "unclear" for c in out)
    assert [c.name for c in out] == ["A", "B"]


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
