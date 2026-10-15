from pipeline.app.state import severity_class


def test_cert_expiring_is_good_timing():
    assert severity_class(20, 70) == "Good timing"
    assert severity_class(70, 10) == "Urgent"
    assert severity_class(0, 0) == "Buying signal"
    assert severity_class(15, 5) == "Hygiene"
