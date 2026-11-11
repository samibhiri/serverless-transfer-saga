"""Unit tests of the accounts API, with DynamoDB mocked by moto (no AWS access)."""

import json
import uuid

import boto3
import pytest
from moto import mock_aws

import app

TABLE_NAME = "accounts-test"


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    # Fake credentials: tests must never reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")
    monkeypatch.setenv("ACCOUNTS_TABLE_NAME", TABLE_NAME)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def table():
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "account_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "account_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        app._table = None  # force the handler to bind to the mocked table
        yield
        app._table = None


# Cognito "sub" claims of two users, as set by the API Gateway authorizer.
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def identity(user_id):
    """requestContext added by the Cognito authorizer after verifying the token."""
    if user_id is None:
        return {}
    return {"requestContext": {"authorizer": {"claims": {"sub": user_id}}}}


def post_account(body, user=ALICE):
    raw = body if isinstance(body, str) else json.dumps(body)
    event = {"httpMethod": "POST", "resource": "/accounts", "body": raw, **identity(user)}
    return app.handler(event, None)


def get_account(account_id, user=ALICE):
    event = {
        "httpMethod": "GET",
        "resource": "/accounts/{account_id}",
        "pathParameters": {"account_id": account_id},
        **identity(user),
    }
    return app.handler(event, None)


def body_of(response):
    return json.loads(response["body"])


# --- Create an account --------------------------------------------------------


def test_create_account_returns_201_with_the_new_account(table):
    response = post_account({"owner": "Alice", "initial_balance": 10_000})

    assert response["statusCode"] == 201
    account = body_of(response)
    assert uuid.UUID(account["account_id"])
    assert account["owner_id"] == ALICE
    assert account["owner"] == "Alice"
    assert account["balance"] == 10_000
    assert account["currency"] == "EUR"


def test_create_account_defaults_to_zero_balance(table):
    response = post_account({"owner": "Bob"})

    assert response["statusCode"] == 201
    assert body_of(response)["balance"] == 0


def test_created_account_can_be_read_back(table):
    created = body_of(post_account({"owner": "Alice", "initial_balance": 2_500}))

    response = get_account(created["account_id"])

    assert response["statusCode"] == 200
    assert body_of(response) == created


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "owner is required"),
        ({"owner": "   "}, "owner is required"),
        ({"owner": "x" * 101}, "owner must be at most"),
        ({"owner": "Alice", "initial_balance": 10.5}, "integer amount in cents"),
        ({"owner": "Alice", "initial_balance": "100"}, "integer amount in cents"),
        ({"owner": "Alice", "initial_balance": True}, "integer amount in cents"),
        ({"owner": "Alice", "initial_balance": -1}, "between 0 and"),
        ({"owner": "Alice", "currency": "USD"}, "currency must be one of"),
    ],
)
def test_create_account_rejects_invalid_input(table, payload, message):
    response = post_account(payload)

    assert response["statusCode"] == 400
    assert message in body_of(response)["message"]


@pytest.mark.parametrize("raw_body", ["not json", "[1, 2]", ""])
def test_create_account_rejects_malformed_body(table, raw_body):
    response = post_account(raw_body)

    assert response["statusCode"] == 400


# --- Read an account ----------------------------------------------------------


def test_get_unknown_account_returns_404(table):
    response = get_account(str(uuid.uuid4()))

    assert response["statusCode"] == 404


def test_get_account_with_invalid_id_returns_400(table):
    response = get_account("not-a-uuid")

    assert response["statusCode"] == 400


# --- Routing and errors -------------------------------------------------------


def test_unknown_route_returns_404(table):
    response = app.handler({"httpMethod": "DELETE", "resource": "/accounts"}, None)

    assert response["statusCode"] == 404


def test_get_account_without_path_parameter_returns_400(table):
    event = {"httpMethod": "GET", "resource": "/accounts/{account_id}", **identity(ALICE)}

    response = app.handler(event, None)

    assert response["statusCode"] == 400


# --- Authorization: users only access their own accounts (ADR-0007) -----------


def test_owner_is_the_authenticated_caller_not_the_body(table):
    payload = {"owner": "Alice", "owner_id": BOB}  # a client cannot choose the owner

    account = body_of(post_account(payload, user=ALICE))

    assert account["owner_id"] == ALICE


def test_user_cannot_read_another_users_account(table):
    alice_account = body_of(post_account({"owner": "Alice", "initial_balance": 50_000}, user=ALICE))

    response = get_account(alice_account["account_id"], user=BOB)

    assert response["statusCode"] == 404
    assert "50000" not in response["body"]


def test_foreign_and_unknown_accounts_get_the_same_answer(table):
    alice_account = body_of(post_account({"owner": "Alice"}, user=ALICE))

    foreign = get_account(alice_account["account_id"], user=BOB)
    unknown = get_account(str(uuid.uuid4()), user=BOB)

    assert foreign["statusCode"] == unknown["statusCode"] == 404
    assert foreign["body"] == unknown["body"]


@pytest.mark.parametrize(
    "request_context",
    [
        None,
        {},
        {"authorizer": None},
        {"authorizer": {"claims": {}}},
        {"authorizer": {"claims": {"sub": ""}}},
    ],
)
def test_requests_without_caller_identity_are_rejected(table, request_context):
    create = {"httpMethod": "POST", "resource": "/accounts", "body": json.dumps({"owner": "Alice"})}
    read = {
        "httpMethod": "GET",
        "resource": "/accounts/{account_id}",
        "pathParameters": {"account_id": str(uuid.uuid4())},
    }
    if request_context is not None:
        create["requestContext"] = request_context
        read["requestContext"] = request_context

    assert app.handler(create, None)["statusCode"] == 401
    assert app.handler(read, None)["statusCode"] == 401


def test_unexpected_error_returns_500_without_details(table, monkeypatch):
    def broken_table():
        raise RuntimeError("database connection details")

    monkeypatch.setattr(app, "get_table", broken_table)

    response = post_account({"owner": "Alice"})

    assert response["statusCode"] == 500
    assert "database" not in response["body"]


def test_unexpected_read_error_returns_500(table, monkeypatch):
    def broken_table():
        raise RuntimeError("database connection details")

    monkeypatch.setattr(app, "get_table", broken_table)

    response = get_account(str(uuid.uuid4()))

    assert response["statusCode"] == 500
    assert "database" not in response["body"]
