"""Unit tests of the transfers API.

DynamoDB is mocked by moto; Step Functions is replaced by a fake client that
records the executions started. No AWS access.
"""

import json
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import app

ACCOUNTS_TABLE = "accounts-test"
TRANSFERS_TABLE = "transfers-test"
STATE_MACHINE_ARN = "arn:aws:states:eu-west-3:123456789012:stateMachine:transfer-saga-test"

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


class FakeStepFunctions:
    """Records started executions and enforces unique execution names, like Step Functions."""

    def __init__(self):
        self.executions = []

    def start_execution(self, stateMachineArn, name, input):
        if any(execution["name"] == name for execution in self.executions):
            raise ClientError(
                {"Error": {"Code": "ExecutionAlreadyExists", "Message": "already exists"}},
                "StartExecution",
            )
        self.executions.append({"stateMachineArn": stateMachineArn, "name": name, "input": json.loads(input)})
        return {"executionArn": f"{stateMachineArn}:{name}"}


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    # Fake credentials: tests must never reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")
    monkeypatch.setenv("ACCOUNTS_TABLE_NAME", ACCOUNTS_TABLE)
    monkeypatch.setenv("TRANSFERS_TABLE_NAME", TRANSFERS_TABLE)
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def sfn():
    return FakeStepFunctions()


@pytest.fixture
def aws(sfn):
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
        app._resources["sfn"] = sfn
        yield
        app._resources.clear()


def add_account(owner_id, balance=10_000, currency="EUR"):
    account_id = str(uuid.uuid4())
    boto3.resource("dynamodb").Table(ACCOUNTS_TABLE).put_item(
        Item={"account_id": account_id, "owner_id": owner_id, "owner": "x", "balance": balance, "currency": currency}
    )
    return account_id


def identity(user_id):
    if user_id is None:
        return {}
    return {"requestContext": {"authorizer": {"claims": {"sub": user_id}}}}


def post_transfer(body, key="key-1", user=ALICE):
    raw = body if isinstance(body, str) else json.dumps(body)
    headers = {} if key is None else {"Idempotency-Key": key}
    event = {"httpMethod": "POST", "resource": "/transfers", "headers": headers, "body": raw, **identity(user)}
    return app.handler(event, None)


def get_transfer(transfer_id, user=ALICE):
    event = {
        "httpMethod": "GET",
        "resource": "/transfers/{transfer_id}",
        "pathParameters": {"transfer_id": transfer_id},
        **identity(user),
    }
    return app.handler(event, None)


def body_of(response):
    return json.loads(response["body"])


@pytest.fixture
def accounts(aws):
    """Alice owns two accounts; Bob owns one."""
    return {
        "alice_1": add_account(ALICE, balance=50_000),
        "alice_2": add_account(ALICE),
        "bob_1": add_account(BOB),
    }


def request_from(accounts, source="alice_1", target="bob_1", amount=1_000):
    return {"source_account_id": accounts[source], "target_account_id": accounts[target], "amount": amount}


# --- Create a transfer ------------------------------------------------------------


def test_create_transfer_returns_202_and_starts_the_saga(accounts, sfn):
    response = post_transfer(request_from(accounts))

    assert response["statusCode"] == 202
    transfer = body_of(response)
    assert transfer["status"] == "PENDING"
    assert transfer["owner_id"] == ALICE
    assert transfer["amount"] == 1_000
    assert transfer["currency"] == "EUR"
    assert sfn.executions == [
        {
            "stateMachineArn": STATE_MACHINE_ARN,
            "name": transfer["transfer_id"],
            "input": {"transfer_id": transfer["transfer_id"]},
        }
    ]


def test_transfer_between_own_accounts_is_allowed(accounts):
    response = post_transfer(request_from(accounts, source="alice_1", target="alice_2"))

    assert response["statusCode"] == 202


def test_created_transfer_can_be_read_back(accounts):
    created = body_of(post_transfer(request_from(accounts)))

    response = get_transfer(created["transfer_id"])

    assert response["statusCode"] == 200
    assert body_of(response) == created


# --- Idempotency (ADR-0008) -----------------------------------------------------------


def test_same_key_and_payload_returns_the_same_transfer_once(accounts, sfn):
    first = body_of(post_transfer(request_from(accounts), key="k1"))
    second_response = post_transfer(request_from(accounts), key="k1")

    assert second_response["statusCode"] == 202
    assert body_of(second_response)["transfer_id"] == first["transfer_id"]
    assert len(sfn.executions) == 1


def test_same_key_with_a_different_payload_returns_422(accounts, sfn):
    post_transfer(request_from(accounts, amount=1_000), key="k1")

    response = post_transfer(request_from(accounts, amount=2_000), key="k1")

    assert response["statusCode"] == 422
    assert len(sfn.executions) == 1


def test_different_keys_create_different_transfers(accounts, sfn):
    first = body_of(post_transfer(request_from(accounts), key="k1"))
    second = body_of(post_transfer(request_from(accounts), key="k2"))

    assert first["transfer_id"] != second["transfer_id"]
    assert len(sfn.executions) == 2


def test_same_key_used_by_two_users_creates_two_transfers(accounts):
    alice_transfer = body_of(post_transfer(request_from(accounts), key="k1", user=ALICE))
    bob_transfer = body_of(post_transfer(request_from(accounts, source="bob_1", target="alice_1"), key="k1", user=BOB))

    assert alice_transfer["transfer_id"] != bob_transfer["transfer_id"]


def test_replay_restarts_a_saga_that_was_never_started(accounts, sfn):
    # First attempt: the transfer is recorded, but starting the Saga fails.
    def broken_start(**kwargs):
        raise ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "down"}}, "StartExecution")

    sfn.start_execution = broken_start
    assert post_transfer(request_from(accounts), key="k1")["statusCode"] == 500

    # Retry with the same key: the Saga is started this time.
    del sfn.start_execution  # back to the class method
    response = post_transfer(request_from(accounts), key="k1")

    assert response["statusCode"] == 202
    assert len(sfn.executions) == 1


def test_idempotency_header_name_is_case_insensitive(accounts, sfn):
    event_headers = {"idempotency-key": "k1"}
    event = {
        "httpMethod": "POST",
        "resource": "/transfers",
        "headers": event_headers,
        "body": json.dumps(request_from(accounts)),
        **identity(ALICE),
    }

    assert app.handler(event, None)["statusCode"] == 202


@pytest.mark.parametrize("key", [None, "", "   ", "x" * 256, "bad\nkey"])
def test_missing_or_invalid_idempotency_key_returns_400(accounts, sfn, key):
    response = post_transfer(request_from(accounts), key=key)

    assert response["statusCode"] == 400
    assert "Idempotency-Key" in body_of(response)["message"]
    assert sfn.executions == []


# --- Validation and business rules -------------------------------------------------------


@pytest.mark.parametrize(
    "amount, message",
    [
        (0, "between 1 and"),
        (-100, "between 1 and"),
        (100_000_001, "between 1 and"),
        (10.5, "integer amount in cents"),
        ("100", "integer amount in cents"),
        (True, "integer amount in cents"),
    ],
)
def test_invalid_amount_returns_400(accounts, amount, message):
    response = post_transfer(request_from(accounts, amount=amount))

    assert response["statusCode"] == 400
    assert message in body_of(response)["message"]


def test_same_source_and_target_returns_400(accounts):
    response = post_transfer(request_from(accounts, source="alice_1", target="alice_1"))

    assert response["statusCode"] == 400


@pytest.mark.parametrize("field", ["source_account_id", "target_account_id"])
def test_invalid_account_id_returns_400(accounts, field):
    payload = request_from(accounts)
    payload[field] = "not-a-uuid"

    assert post_transfer(payload)["statusCode"] == 400


@pytest.mark.parametrize("raw_body", ["not json", "[1, 2]", ""])
def test_malformed_body_returns_400(accounts, raw_body):
    assert post_transfer(raw_body)["statusCode"] == 400


def test_currency_mismatch_returns_400(aws):
    source = add_account(ALICE, currency="EUR")
    target = add_account(BOB, currency="USD")

    response = post_transfer({"source_account_id": source, "target_account_id": target, "amount": 100})

    assert response["statusCode"] == 400


# --- Authorization (ADR-0007) ---------------------------------------------------------------


def test_cannot_transfer_from_someone_elses_account(accounts, sfn):
    response = post_transfer(request_from(accounts, source="bob_1", target="alice_1"), user=ALICE)

    assert response["statusCode"] == 404
    assert body_of(response)["message"] == "Source account not found"
    assert sfn.executions == []


def test_unknown_source_and_foreign_source_get_the_same_answer(accounts):
    foreign = post_transfer(request_from(accounts, source="bob_1", target="alice_1"), key="k1")
    payload = request_from(accounts)
    payload["source_account_id"] = str(uuid.uuid4())
    unknown = post_transfer(payload, key="k2")

    assert foreign["statusCode"] == unknown["statusCode"] == 404
    assert foreign["body"] == unknown["body"]


def test_unknown_target_returns_404(accounts, sfn):
    payload = request_from(accounts)
    payload["target_account_id"] = str(uuid.uuid4())

    response = post_transfer(payload)

    assert response["statusCode"] == 404
    assert sfn.executions == []


def test_user_cannot_read_another_users_transfer(accounts):
    alice_transfer = body_of(post_transfer(request_from(accounts), user=ALICE))

    response = get_transfer(alice_transfer["transfer_id"], user=BOB)

    assert response["statusCode"] == 404


def test_get_unknown_transfer_returns_404(accounts):
    assert get_transfer(str(uuid.uuid4()))["statusCode"] == 404


def test_get_transfer_with_invalid_id_returns_400(accounts):
    assert get_transfer("not-a-uuid")["statusCode"] == 400


def test_requests_without_caller_identity_are_rejected(accounts, sfn):
    assert post_transfer(request_from(accounts), user=None)["statusCode"] == 401
    assert get_transfer(str(uuid.uuid4()), user=None)["statusCode"] == 401
    assert sfn.executions == []


# --- Routing and errors ----------------------------------------------------------------------


def test_unknown_route_returns_404(aws):
    assert app.handler({"httpMethod": "DELETE", "resource": "/transfers"}, None)["statusCode"] == 404


def test_unexpected_error_returns_500_without_details(accounts, monkeypatch):
    def broken_table():
        raise RuntimeError("database connection details")

    monkeypatch.setattr(app, "transfers_table", broken_table)

    response = post_transfer(request_from(accounts))

    assert response["statusCode"] == 500
    assert "database" not in response["body"]
