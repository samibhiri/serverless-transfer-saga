"""Accounts API.

AWS Lambda handler behind Amazon API Gateway (REST API, Lambda proxy integration).
One Lambda function serves every route of the accounts service (see ADR-0004).

Routes:
    POST /accounts                 create an account
    GET  /accounts/{account_id}    read an account and its balance

Amounts are integers in minor units (cents): 12345 means 123.45 EUR.

Authorization (ADR-0007): an account belongs to the Cognito user who created it
(owner_id = the "sub" claim of the caller's token). A user can only read their
own accounts; other accounts are reported as not found.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SUPPORTED_CURRENCIES = {"EUR"}
MAX_OWNER_LENGTH = 100
MAX_INITIAL_BALANCE = 100_000_000  # 1,000,000.00 in minor units

_table = None


def get_table():
    """Return the DynamoDB table, created once per Lambda execution environment."""
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["ACCOUNTS_TABLE_NAME"])
    return _table


class ValidationError(Exception):
    """Invalid client input, returned as HTTP 400."""


class UnauthenticatedError(Exception):
    """No verified caller identity in the request, returned as HTTP 401."""


def caller_id(event):
    """Return the verified identity of the caller: the Cognito "sub" claim.

    The claims are set by the API Gateway Cognito authorizer after it has
    verified the token; they cannot be supplied by the client in the body.
    Defense in depth: refuse the request if they are missing.
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise UnauthenticatedError()
    return sub


def handler(event, context):
    route = (event.get("httpMethod"), event.get("resource"))
    try:
        if route == ("POST", "/accounts"):
            return create_account(event)
        if route == ("GET", "/accounts/{account_id}"):
            return get_account(event)
        return response(404, {"message": "Route not found"})
    except UnauthenticatedError:
        return response(401, {"message": "Unauthorized"})
    except ValidationError as error:
        return response(400, {"message": str(error)})
    except Exception:
        # Details go to the logs only, never to the client.
        logger.exception("Unhandled error on route %s %s", *route)
        return response(500, {"message": "Internal server error"})


def create_account(event):
    owner_id = caller_id(event)
    payload = parse_body(event)
    owner = validate_owner(payload.get("owner"))
    initial_balance = validate_amount(payload.get("initial_balance", 0))
    currency = validate_currency(payload.get("currency", "EUR"))

    account = {
        "account_id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "owner": owner,
        "balance": initial_balance,
        "currency": currency,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # The condition guarantees that an existing account is never overwritten.
    get_table().put_item(
        Item=account,
        ConditionExpression="attribute_not_exists(account_id)",
    )
    logger.info("Account created: %s", account["account_id"])
    return response(201, account)


def get_account(event):
    requester_id = caller_id(event)
    account_id = (event.get("pathParameters") or {}).get("account_id", "")
    try:
        uuid.UUID(account_id)
    except ValueError:
        raise ValidationError("account_id must be a UUID") from None

    item = get_table().get_item(Key={"account_id": account_id}).get("Item")
    # Same answer for "does not exist" and "belongs to someone else":
    # a 403 would confirm that the account exists (no information leak).
    if item is None or item.get("owner_id") != requester_id:
        return response(404, {"message": "Account not found"})
    return response(200, item)


def parse_body(event):
    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        raise ValidationError("Body must be valid JSON") from None
    if not isinstance(payload, dict):
        raise ValidationError("Body must be a JSON object")
    return payload


def validate_owner(owner):
    if not isinstance(owner, str) or not owner.strip():
        raise ValidationError("owner is required")
    if len(owner) > MAX_OWNER_LENGTH:
        raise ValidationError(f"owner must be at most {MAX_OWNER_LENGTH} characters")
    return owner.strip()


def validate_amount(amount):
    # bool is a subclass of int in Python: reject it explicitly.
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValidationError("initial_balance must be an integer amount in cents")
    if not 0 <= amount <= MAX_INITIAL_BALANCE:
        raise ValidationError(f"initial_balance must be between 0 and {MAX_INITIAL_BALANCE}")
    return amount


def validate_currency(currency):
    if currency not in SUPPORTED_CURRENCIES:
        raise ValidationError(f"currency must be one of {sorted(SUPPORTED_CURRENCIES)}")
    return currency


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(value):
    # DynamoDB returns numbers as Decimal; our amounts are always integers.
    if isinstance(value, Decimal):
        return int(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
