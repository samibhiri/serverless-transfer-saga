"""Steps of the transfer Saga (ADR-0008).

One AWS Lambda function, invoked by the Step Functions state machine with:
    {"action": "debit" | "credit" | "refund", "transfer_id": "..."}

Each step moves money with ONE DynamoDB transaction that updates a balance AND
marks the step as done on the transfer (debited_at, credited_at, refunded_at),
on the condition that the mark does not exist yet. The mark and the money move
are atomic: "mark present" always means "step done". A retried step finds the
mark, changes nothing and reports success: steps are idempotent.

Errors raised to Step Functions (the error name is the exception class name):
    InsufficientFunds   business error: the debit is refused     -> FAILED
    CreditRejected      business error: the credit is refused    -> compensation
    StepNotAllowed      the transfer is not in a state allowing the step (bug or race)
    TransientError      temporary problem: Step Functions retries the step
"""

import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_resources = {}


def dynamodb():
    if "client" not in _resources:
        _resources["client"] = boto3.client("dynamodb")
    return _resources["client"]


def accounts_table_name():
    return os.environ["ACCOUNTS_TABLE_NAME"]


def transfers_table_name():
    return os.environ["TRANSFERS_TABLE_NAME"]


class InsufficientFunds(Exception):
    """The source account does not exist or its balance is too low."""


class CreditRejected(Exception):
    """The target account cannot receive the money (missing, closed or other currency)."""


class StepNotAllowed(Exception):
    """The transfer is not in a state that allows this step."""


class TransientError(Exception):
    """A temporary problem (transaction conflict, throttling): the step can be retried."""


def handler(event, context):
    action = event.get("action")
    transfer_id = event.get("transfer_id")
    steps = {"debit": debit, "credit": credit, "refund": refund}
    if action not in steps or not isinstance(transfer_id, str):
        raise ValueError(f"Invalid step request: {event!r}")

    result = steps[action](transfer_id)
    logger.info("Step %s of transfer %s: %s", action, transfer_id, result)
    return {"transfer_id": transfer_id, "action": action, "result": result}


# --- Steps -----------------------------------------------------------------------


def debit(transfer_id):
    transfer = load_transfer(transfer_id)
    if "debited_at" in transfer:
        return "already_done"
    amount, currency = transfer["amount"]["N"], transfer["currency"]["S"]

    return run_step(
        transfer_id,
        mark="debited_at",
        account_update={
            "TableName": accounts_table_name(),
            "Key": {"account_id": transfer["source_account_id"]},
            "UpdateExpression": "SET balance = balance - :amount",
            # A balance never becomes negative.
            "ConditionExpression": "attribute_exists(account_id) AND currency = :currency AND balance >= :amount",
            "ExpressionAttributeValues": {":amount": {"N": amount}, ":currency": {"S": currency}},
        },
        # Debit only a transfer still in progress.
        transfer_condition="attribute_not_exists(debited_at) AND #status = :pending",
        transfer_values={":pending": {"S": "PENDING"}},
        account_refused=InsufficientFunds("Insufficient funds on the source account"),
    )


def credit(transfer_id):
    transfer = load_transfer(transfer_id)
    if "credited_at" in transfer:
        return "already_done"
    amount, currency = transfer["amount"]["N"], transfer["currency"]["S"]

    return run_step(
        transfer_id,
        mark="credited_at",
        account_update={
            "TableName": accounts_table_name(),
            "Key": {"account_id": transfer["target_account_id"]},
            "UpdateExpression": "SET balance = balance + :amount",
            # A closed account cannot receive money.
            "ConditionExpression": (
                "attribute_exists(account_id) AND currency = :currency "
                "AND (attribute_not_exists(#account_status) OR #account_status <> :closed)"
            ),
            "ExpressionAttributeNames": {"#account_status": "status"},
            "ExpressionAttributeValues": {
                ":amount": {"N": amount},
                ":currency": {"S": currency},
                ":closed": {"S": "CLOSED"},
            },
        },
        # Credit only after a debit, and never after a refund.
        transfer_condition=(
            "attribute_not_exists(credited_at) AND attribute_exists(debited_at) "
            "AND attribute_not_exists(refunded_at) AND #status = :pending"
        ),
        transfer_values={":pending": {"S": "PENDING"}},
        account_refused=CreditRejected("The target account cannot receive the transfer"),
    )


def refund(transfer_id):
    """Compensation of the debit: give the money back to the source account."""
    transfer = load_transfer(transfer_id)
    if "refunded_at" in transfer:
        return "already_done"
    amount = transfer["amount"]["N"]

    return run_step(
        transfer_id,
        mark="refunded_at",
        account_update={
            "TableName": accounts_table_name(),
            "Key": {"account_id": transfer["source_account_id"]},
            "UpdateExpression": "SET balance = balance + :amount",
            "ConditionExpression": "attribute_exists(account_id)",
            "ExpressionAttributeValues": {":amount": {"N": amount}},
        },
        # Refund only money that was debited and not credited.
        transfer_condition=(
            "attribute_not_exists(refunded_at) AND attribute_exists(debited_at) "
            "AND attribute_not_exists(credited_at) AND #status = :pending"
        ),
        transfer_values={":pending": {"S": "PENDING"}},
        # The source account always exists (accounts cannot be deleted): a refusal is a bug.
        account_refused=StepNotAllowed("The source account cannot be refunded"),
    )


# --- Shared mechanics ----------------------------------------------------------------


def load_transfer(transfer_id):
    item = dynamodb().get_item(
        TableName=transfers_table_name(),
        Key={"transfer_id": {"S": transfer_id}},
        ConsistentRead=True,
    ).get("Item")
    if item is None:
        raise StepNotAllowed(f"Transfer {transfer_id} not found")
    return item


def run_step(transfer_id, mark, account_update, transfer_condition, transfer_values, account_refused):
    """Move the money and mark the step as done, in one transaction."""
    now = datetime.now(timezone.utc).isoformat()
    transfer_update = {
        "TableName": transfers_table_name(),
        "Key": {"transfer_id": {"S": transfer_id}},
        "UpdateExpression": f"SET {mark} = :now, updated_at = :now",
        "ConditionExpression": transfer_condition,
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {":now": {"S": now}, **transfer_values},
    }
    try:
        dynamodb().transact_write_items(
            TransactItems=[{"Update": account_update}, {"Update": transfer_update}]
        )
        return "done"
    except ClientError as error:
        if error.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = [reason.get("Code", "None") for reason in error.response.get("CancellationReasons", [])]

    # The transaction was cancelled: nothing was written. Find out why.
    if "TransactionConflict" in reasons:
        raise TransientError(f"Transaction conflict on transfer {transfer_id}")

    current = load_transfer(transfer_id)
    if mark in current:
        # A previous attempt already did this step (for example, its response was lost).
        return "already_done"
    if len(reasons) == 2 and reasons[1] == "ConditionalCheckFailed":
        raise StepNotAllowed(f"Transfer {transfer_id} does not allow the step {mark}")
    if reasons and reasons[0] == "ConditionalCheckFailed":
        raise account_refused
    raise TransientError(f"Transaction cancelled for transfer {transfer_id}: {reasons}")
