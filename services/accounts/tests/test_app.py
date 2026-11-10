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


def post_account(body):
    raw = body if isinstance(body, str) else json.dumps(body)
    return app.handler({"httpMethod": "POST", "resource": "/accounts", "body": raw}, None)


def get_account(account_id):
    event = {
        "httpMethod": "GET",
        "resource": "/accounts/{account_id}",
        "pathParameters": {"account_id": account_id},
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
    response = app.handler({"httpMethod": "GET", "resource": "/accounts/{account_id}"}, None)

    assert response["statusCode"] == 400


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
