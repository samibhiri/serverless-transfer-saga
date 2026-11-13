"""Transfers API.

AWS Lambda handler behind Amazon API Gateway (REST API, Lambda proxy integration).
One Lambda function serves every route of the transfers service (ADR-0004).

Routes:
    POST /transfers                  request a transfer (asynchronous, 202)
    GET  /transfers/{transfer_id}    read a transfer and its status

A transfer is processed by the transfer Saga, an AWS Step Functions state
machine (ADR-0008). This function only validates the request, records the
transfer as PENDING and starts the execution.

Idempotency (ADR-0008): the Idempotency-Key header is required. The transfer_id
is a UUID v5 of the caller and the key, so the same request sent twice maps to
the same transfer. The execution is named after the transfer_id.

Authorization (ADR-0007): the caller must own the source account, and can only
read their own transfers.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

MAX_TRANSFER_AMOUNT = 100_000_000  # 1,000,000.00 in minor units
MAX_IDEMPOTENCY_KEY_LENGTH = 255

# Fixed namespace of the project: transfer_id = uuid5(namespace, owner_id + key).
TRANSFER_ID_NAMESPACE = uuid.UUID("6f1c3c4e-2d1b-4c7a-9f3e-8a4b5c6d7e8f")

# Fields that define a transfer request: a reused idempotency key must match them.
REQUEST_FIELDS = ("source_account_id", "target_account_id", "amount")

_resources = {}


def accounts_table():
    if "accounts" not in _resources:
        _resources["accounts"] = boto3.resource("dynamodb").Table(os.environ["ACCOUNTS_TABLE_NAME"])
    return _resources["accounts"]


def transfers_table():
    if "transfers" not in _resources:
        _resources["transfers"] = boto3.resource("dynamodb").Table(os.environ["TRANSFERS_TABLE_NAME"])
    return _resources["transfers"]


def stepfunctions():
    if "sfn" not in _resources:
        _resources["sfn"] = boto3.client("stepfunctions")
    return _resources["sfn"]


class ApiError(Exception):
    """An error returned to the client with an HTTP status and a message."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def handler(event, context):
    route = (event.get("httpMethod"), event.get("resource"))
    try:
        if route == ("POST", "/transfers"):
            return create_transfer(event)
        if route == ("GET", "/transfers/{transfer_id}"):
            return get_transfer(event)
        return response(404, {"message": "Route not found"})
    except ApiError as error:
        return response(error.status_code, {"message": error.message})
    except Exception:
        # Details go to the logs only, never to the client.
        logger.exception("Unhandled error on route %s %s", *route)
        return response(500, {"message": "Internal server error"})


# --- POST /transfers ---------------------------------------------------------------


def create_transfer(event):
    owner_id = caller_id(event)
    key = idempotency_key(event)
    request = validate_request(parse_body(event))
    transfer_id = str(uuid.uuid5(TRANSFER_ID_NAMESPACE, f"{owner_id}:{key}"))

    # Same key already used by this caller: return the existing transfer.
    existing = transfers_table().get_item(Key={"transfer_id": transfer_id}).get("Item")
    if existing is not None:
        return replay(existing, request)

    source = load_account(request["source_account_id"])
    if source is None or source.get("owner_id") != owner_id:
        raise ApiError(404, "Source account not found")
    target = load_account(request["target_account_id"])
    if target is None:
        raise ApiError(404, "Target account not found")
    if source["currency"] != target["currency"]:
        raise ApiError(400, "Source and target accounts must have the same currency")

    now = datetime.now(timezone.utc).isoformat()
    transfer = {
        "transfer_id": transfer_id,
        "owner_id": owner_id,
        **request,
        "currency": source["currency"],
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
    }
    try:
        transfers_table().put_item(
            Item=transfer,
            ConditionExpression="attribute_not_exists(transfer_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # A concurrent request with the same key won the race.
        existing = transfers_table().get_item(Key={"transfer_id": transfer_id}, ConsistentRead=True)["Item"]
        return replay(existing, request)

    start_saga(transfer_id)
    logger.info("Transfer requested: %s", transfer_id)
    return response(202, transfer)


def replay(existing, request):
    """Answer a request whose idempotency key was already used."""
    if any(existing.get(field) != request[field] for field in REQUEST_FIELDS):
        raise ApiError(422, "Idempotency-Key already used with a different request")
    # Self-healing: if a previous attempt stopped between recording the transfer
    # and starting the Saga, start it now (the execution name prevents duplicates).
    if existing.get("status") == "PENDING":
        start_saga(existing["transfer_id"])
    return response(202, existing)


def start_saga(transfer_id):
    try:
        stepfunctions().start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=transfer_id,
            input=json.dumps({"transfer_id": transfer_id}),
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        logger.info("Saga already started for transfer %s", transfer_id)


def load_account(account_id):
    return accounts_table().get_item(Key={"account_id": account_id}).get("Item")


# --- GET /transfers/{transfer_id} --------------------------------------------------------


def get_transfer(event):
    requester_id = caller_id(event)
    transfer_id = validate_uuid((event.get("pathParameters") or {}).get("transfer_id"), "transfer_id")

    item = transfers_table().get_item(Key={"transfer_id": transfer_id}).get("Item")
    # Same answer for "does not exist" and "belongs to someone else" (ADR-0007).
    if item is None or item.get("owner_id") != requester_id:
        raise ApiError(404, "Transfer not found")
    return response(200, item)


# --- Request parsing and validation --------------------------------------------------------


def caller_id(event):
    """Verified identity of the caller, set by the API Gateway Cognito authorizer."""
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ApiError(401, "Unauthorized")
    return sub


def idempotency_key(event):
    headers = event.get("headers") or {}
    key = next((value for name, value in headers.items() if name.lower() == "idempotency-key"), None)
    if not isinstance(key, str) or not key.strip():
        raise ApiError(400, "Idempotency-Key header is required")
    key = key.strip()
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH or not key.isprintable():
        raise ApiError(400, f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} printable characters")
    return key


def parse_body(event):
    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        raise ApiError(400, "Body must be valid JSON") from None
    if not isinstance(payload, dict):
        raise ApiError(400, "Body must be a JSON object")
    return payload


def validate_request(payload):
    source = validate_uuid(payload.get("source_account_id"), "source_account_id")
    target = validate_uuid(payload.get("target_account_id"), "target_account_id")
    if source == target:
        raise ApiError(400, "source_account_id and target_account_id must be different")

    amount = payload.get("amount")
    # bool is a subclass of int in Python: reject it explicitly.
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ApiError(400, "amount must be an integer amount in cents")
    if not 0 < amount <= MAX_TRANSFER_AMOUNT:
        raise ApiError(400, f"amount must be between 1 and {MAX_TRANSFER_AMOUNT}")

    return {"source_account_id": source, "target_account_id": target, "amount": amount}


def validate_uuid(value, name):
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise ApiError(400, f"{name} must be a UUID") from None


# --- Response -------------------------------------------------------------------------------


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
