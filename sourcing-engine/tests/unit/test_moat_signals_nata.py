from sourcing.models.company import MoatSignals


def test_nata_fields_default_empty():
    m = MoatSignals()
    assert m.nata_accreditation is False
    assert m.nata_site_count is None
    assert m.nata_service_types == []
    assert m.nata_accreditation_numbers == []
    assert m.nata_states == []
    assert m.nata_multistate is False


def test_nata_fields_populate():
    m = MoatSignals(nata_accreditation=True, nata_site_count=3,
                    nata_states=["NSW", "VIC"], nata_multistate=True)
    assert m.nata_site_count == 3
    assert m.nata_multistate is True
