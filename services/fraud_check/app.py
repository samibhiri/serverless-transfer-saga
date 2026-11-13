"""Simulated external fraud check service.

Stands for a third-party service called by the transfer Saga (ADR-0008).
It is INTENTIONALLY UNRELIABLE, to exercise retries now and a circuit breaker
in phase 5:

- it fails at random with a temporary error (FraudServiceUnavailable), with the
  probability FAILURE_RATE (0.0 to 1.0);
- it rejects any transfer above REJECTION_THRESHOLD (amount in minor units).

Input:  {"transfer_id": "...", "amount": 1000}
Output: {"transfer_id": "...", "decision": "ACCEPTED" | "REJECTED", "reason": "..."}
"""

import logging
import os
import random

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEFAULT_REJECTION_THRESHOLD = 500_000  # 5,000.00 in minor units


class FraudServiceUnavailable(Exception):
    """Temporary failure of the fraud service: the caller may retry."""


def handler(event, context):
    transfer_id = event.get("transfer_id")
    amount = event.get("amount")
    if not isinstance(transfer_id, str) or not isinstance(amount, int) or isinstance(amount, bool):
        raise ValueError(f"Invalid fraud check request: {event!r}")

    failure_rate = float(os.environ.get("FAILURE_RATE", "0"))
    if random.random() < failure_rate:
        logger.warning("Simulated outage for transfer %s", transfer_id)
        raise FraudServiceUnavailable("Fraud service temporarily unavailable")

    threshold = int(os.environ.get("REJECTION_THRESHOLD", DEFAULT_REJECTION_THRESHOLD))
    if amount > threshold:
        logger.info("Transfer %s rejected: amount %s above %s", transfer_id, amount, threshold)
        return {"transfer_id": transfer_id, "decision": "REJECTED", "reason": "Amount above the fraud threshold"}

    return {"transfer_id": transfer_id, "decision": "ACCEPTED", "reason": ""}
