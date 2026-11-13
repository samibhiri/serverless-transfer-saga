"""Unit tests of the simulated fraud check service."""

import pytest

import app


@pytest.fixture(autouse=True)
def reliable_service(monkeypatch):
    monkeypatch.setenv("FAILURE_RATE", "0")
    monkeypatch.setenv("REJECTION_THRESHOLD", "500000")


def check(amount, transfer_id="t-1"):
    return app.handler({"transfer_id": transfer_id, "amount": amount}, None)


def test_amount_at_the_threshold_is_accepted():
    assert check(500_000)["decision"] == "ACCEPTED"


def test_amount_above_the_threshold_is_rejected():
    result = check(500_001)

    assert result["decision"] == "REJECTED"
    assert result["reason"]


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("REJECTION_THRESHOLD", "100")

    assert check(101)["decision"] == "REJECTED"


def test_failure_rate_of_one_always_fails(monkeypatch):
    monkeypatch.setenv("FAILURE_RATE", "1")

    with pytest.raises(app.FraudServiceUnavailable):
        check(100)


def test_failure_rate_produces_some_failures(monkeypatch):
    monkeypatch.setenv("FAILURE_RATE", "0.5")
    outcomes = []
    for _ in range(200):
        try:
            check(100)
            outcomes.append("ok")
        except app.FraudServiceUnavailable:
            outcomes.append("failed")

    assert 0 < outcomes.count("failed") < 200


@pytest.mark.parametrize(
    "event",
    [{}, {"transfer_id": "t-1"}, {"transfer_id": "t-1", "amount": "100"}, {"transfer_id": "t-1", "amount": True}],
)
def test_invalid_request_is_refused(event):
    with pytest.raises(ValueError):
        app.handler(event, None)
