import pytest

from pipeline.ai.cost import estimate_cost


def test_known_rate():
    assert estimate_cost("gpt-4.1", 1_000_000, 1_000_000) == pytest.approx(10.0)
    assert estimate_cost("gpt-4.1-mini", 1000, 100) == pytest.approx(0.00056)
    assert estimate_cost("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert estimate_cost("gpt-6-luna", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_unknown_model_raises():
    with pytest.raises(ValueError):
        estimate_cost("gpt-unknown", 1, 1)
