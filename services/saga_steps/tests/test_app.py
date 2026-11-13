"""Unit tests of the Saga steps, with DynamoDB mocked by moto (no AWS access).

The key property under test: every step is idempotent. Running a step twice
moves the money once.
"""

import uuid

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import app

ACCOUNTS_TABLE = "accounts-test"
TRANSFERS_TABLE = "transfers-test"


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")
    monkeypatch.setenv("ACCOUNTS_TABLE_NAME", ACCOUNTS_TABLE)
    monkeypatch.setenv("TRANSFERS_TABLE_NAME", TRANSFERS_TABLE)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        client = boto3.client("dynamodb")
        for name, key in ((ACCOUNTS_TABLE, "account_id"), (TRANSFERS_TABLE, "transfer_id")):
            client.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        app._resources.clear()
        yield
        app._resources.clear()


def accounts():
    return boto3.resource("dynamodb").Table(ACCOUNTS_TABLE)


def transfers():
    return boto3.resource("dynamodb").Table(TRANSFERS_TABLE)


def add_account(balance, currency="EUR", status=None):
    account_id = str(uuid.uuid4())
    item = {"account_id": account_id, "owner_id": "someone", "balance": balance, "currency": currency}
    if status:
        item["status"] = status
    accounts().put_item(Item=item)
    return account_id


def balance(account_id):
    return int(accounts().get_item(Key={"account_id": account_id})["Item"]["balance"])


def transfer(transfer_id):
    return transfers().get_item(Key={"transfer_id": transfer_id})["Item"]


def add_transfer(source, target, amount, **marks):
    transfer_id = str(uuid.uuid4())
    transfers().put_item(
        Item={
            "transfer_id": transfer_id,
            "owner_id": "someone",
            "source_account_id": source,
            "target_account_id": target,
            "amount": amount,
            "currency": "EUR",
            "status": marks.pop("status", "PENDING"),
            **marks,
        }
    )
    return transfer_id


def step(action, transfer_id):
    return app.handler({"action": action, "transfer_id": transfer_id}, None)


@pytest.fixture
def pair(aws):
    """A source account with 10,000 and a target account with 500, and a transfer of 3,000."""
    source = add_account(10_000)
    target = add_account(500)
    transfer_id = add_transfer(source, target, 3_000)
    return source, target, transfer_id


# --- Debit ------------------------------------------------------------------------------


def test_debit_moves_money_and_marks_the_step(pair):
    source, target, transfer_id = pair

    result = step("debit", transfer_id)

    assert result == {"transfer_id": transfer_id, "action": "debit", "result": "done"}
    assert balance(source) == 7_000
    assert balance(target) == 500
    assert "debited_at" in transfer(transfer_id)


def test_debit_twice_debits_once(pair):
    source, _, transfer_id = pair

    step("debit", transfer_id)
    second = step("debit", transfer_id)

    assert second["result"] == "already_done"
    assert balance(source) == 7_000


def test_debit_with_insufficient_funds_changes_nothing(aws):
    source = add_account(1_000)
    transfer_id = add_transfer(source, add_account(0), 3_000)

    with pytest.raises(app.InsufficientFunds):
        step("debit", transfer_id)

    assert balance(source) == 1_000
    assert "debited_at" not in transfer(transfer_id)


def test_debit_of_the_exact_balance_is_allowed(aws):
    source = add_account(3_000)
    transfer_id = add_transfer(source, add_account(0), 3_000)

    step("debit", transfer_id)

    assert balance(source) == 0


def test_debit_of_a_finished_transfer_is_not_allowed(aws):
    source = add_account(10_000)
    transfer_id = add_transfer(source, add_account(0), 3_000, status="REJECTED")

    with pytest.raises(app.StepNotAllowed):
        step("debit", transfer_id)

    assert balance(source) == 10_000


@pytest.mark.parametrize("action, mark", [("debit", "debited_at"), ("credit", "credited_at"), ("refund", "refunded_at")])
def test_concurrent_attempt_is_stopped_by_the_transaction_condition(pair, action, mark):
    """Two attempts of the same step overlap: both read the transfer before either writes.

    The first read ("not done yet") is stale when the second attempt writes:
    only the condition inside the transaction prevents moving the money twice.
    """
    source, target, transfer_id = pair
    if action in ("credit", "refund"):
        step("debit", transfer_id)
    stale = app.dynamodb().get_item(TableName=TRANSFERS_TABLE, Key={"transfer_id": {"S": transfer_id}})
    step(action, transfer_id)  # the other attempt completes the step meanwhile
    balances_after_first = (balance(source), balance(target))

    real_client = app.dynamodb()
    reads = []

    class StaleFirstRead:
        def get_item(self, **kwargs):
            reads.append(kwargs)
            return stale if len(reads) == 1 else real_client.get_item(**kwargs)

        def transact_write_items(self, **kwargs):
            return real_client.transact_write_items(**kwargs)

    app._resources["client"] = StaleFirstRead()

    result = step(action, transfer_id)

    assert mark not in stale["Item"]
    assert result["result"] == "already_done"
    assert (balance(source), balance(target)) == balances_after_first


# --- Credit -------------------------------------------------------------------------------


def test_credit_moves_money_and_marks_the_step(pair):
    source, target, transfer_id = pair
    step("debit", transfer_id)

    result = step("credit", transfer_id)

    assert result["result"] == "done"
    assert balance(source) == 7_000
    assert balance(target) == 3_500
    assert "credited_at" in transfer(transfer_id)


def test_credit_twice_credits_once(pair):
    _, target, transfer_id = pair
    step("debit", transfer_id)

    step("credit", transfer_id)
    second = step("credit", transfer_id)

    assert second["result"] == "already_done"
    assert balance(target) == 3_500


def test_credit_before_debit_is_not_allowed(pair):
    _, target, transfer_id = pair

    with pytest.raises(app.StepNotAllowed):
        step("credit", transfer_id)

    assert balance(target) == 500


def test_credit_to_a_closed_account_is_rejected(aws):
    source = add_account(10_000)
    target = add_account(500, status="CLOSED")
    transfer_id = add_transfer(source, target, 3_000)
    step("debit", transfer_id)

    with pytest.raises(app.CreditRejected):
        step("credit", transfer_id)

    assert balance(target) == 500
    assert "credited_at" not in transfer(transfer_id)


def test_credit_to_an_account_in_another_currency_is_rejected(aws):
    source = add_account(10_000)
    target = add_account(500, currency="USD")
    transfer_id = add_transfer(source, target, 3_000)
    step("debit", transfer_id)

    with pytest.raises(app.CreditRejected):
        step("credit", transfer_id)


def test_credit_to_a_missing_account_is_rejected(aws):
    source = add_account(10_000)
    transfer_id = add_transfer(source, str(uuid.uuid4()), 3_000)
    step("debit", transfer_id)

    with pytest.raises(app.CreditRejected):
        step("credit", transfer_id)


# --- Refund (compensation) -----------------------------------------------------------------


def test_refund_gives_the_money_back(aws):
    source = add_account(10_000)
    target = add_account(500, status="CLOSED")
    transfer_id = add_transfer(source, target, 3_000)
    step("debit", transfer_id)

    result = step("refund", transfer_id)

    assert result["result"] == "done"
    assert balance(source) == 10_000
    assert "refunded_at" in transfer(transfer_id)


def test_refund_twice_refunds_once(pair):
    source, _, transfer_id = pair
    step("debit", transfer_id)

    step("refund", transfer_id)
    second = step("refund", transfer_id)

    assert second["result"] == "already_done"
    assert balance(source) == 10_000


def test_refund_without_debit_is_not_allowed(pair):
    source, _, transfer_id = pair

    with pytest.raises(app.StepNotAllowed):
        step("refund", transfer_id)

    assert balance(source) == 10_000


def test_refund_after_credit_is_not_allowed(pair):
    source, _, transfer_id = pair
    step("debit", transfer_id)
    step("credit", transfer_id)

    with pytest.raises(app.StepNotAllowed):
        step("refund", transfer_id)

    assert balance(source) == 7_000


def test_credit_after_refund_is_not_allowed(pair):
    _, target, transfer_id = pair
    step("debit", transfer_id)
    step("refund", transfer_id)

    with pytest.raises(app.StepNotAllowed):
        step("credit", transfer_id)

    assert balance(target) == 500


# --- Money is conserved ---------------------------------------------------------------------


def test_completed_transfer_conserves_money(pair):
    source, target, transfer_id = pair
    total_before = balance(source) + balance(target)

    step("debit", transfer_id)
    step("credit", transfer_id)

    assert balance(source) + balance(target) == total_before


def test_compensated_transfer_restores_every_balance(aws):
    source = add_account(10_000)
    target = add_account(500, status="CLOSED")
    transfer_id = add_transfer(source, target, 3_000)

    step("debit", transfer_id)
    with pytest.raises(app.CreditRejected):
        step("credit", transfer_id)
    step("refund", transfer_id)

    assert (balance(source), balance(target)) == (10_000, 500)


# --- Errors ---------------------------------------------------------------------------------


def test_transaction_conflict_is_a_transient_error(pair, monkeypatch):
    _, _, transfer_id = pair
    real_client = app.dynamodb()

    class ConflictingClient:
        def get_item(self, **kwargs):
            return real_client.get_item(**kwargs)

        def transact_write_items(self, **kwargs):
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "conflict"},
                    "CancellationReasons": [{"Code": "TransactionConflict"}, {"Code": "None"}],
                },
                "TransactWriteItems",
            )

    app._resources["client"] = ConflictingClient()

    with pytest.raises(app.TransientError):
        step("debit", transfer_id)


def test_unknown_transfer_is_not_allowed(aws):
    with pytest.raises(app.StepNotAllowed):
        step("debit", str(uuid.uuid4()))


@pytest.mark.parametrize("event", [{}, {"action": "steal", "transfer_id": "x"}, {"action": "debit"}])
def test_invalid_step_request_is_refused(aws, event):
    with pytest.raises(ValueError):
        app.handler(event, None)
