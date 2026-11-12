# ADR-0008: Transfer Saga orchestrated with AWS Step Functions

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

A transfer moves money between two accounts, after a fraud check performed by an external (simulated) service. It must guarantee that:

- money is never lost or created, even if a step fails halfway;
- a balance never becomes negative;
- a request sent twice (double click, network retry) creates **one** transfer only;
- a step retried after a transient error has **no side effect** the second time.

The accounts live in DynamoDB. A single database transaction cannot include the call to the fraud service, and the steps may take several seconds (retries with backoff). A distributed transaction (two-phase commit) is not available across these components.

## Decision

### 1. A Saga, orchestrated by Step Functions

The transfer is a **Saga**: a sequence of local steps, each with a **compensating action**.

| Step | Action | Compensation |
|---|---|---|
| 1. Fraud check | ask the fraud service | none (no state changed) |
| 2. Debit | debit the source account, if the balance is sufficient | refund the source account |
| 3. Credit | credit the target account | none (last step) |

The Saga is **orchestrated** by an **AWS Step Functions Standard workflow**: the state machine calls each step, retries transient errors, runs the compensation if the credit fails, and records the final status.

Final statuses: `COMPLETED`, `REJECTED` (fraud check refused, no movement), `FAILED` (debit refused, for example insufficient funds; no movement), `COMPENSATED` (credit failed after the debit; the source account was refunded).

### 2. An asynchronous API

- `POST /transfers` validates the request, records the transfer as `PENDING`, starts the state machine and returns **`202 Accepted`** with the `transfer_id`.
- `GET /transfers/{transfer_id}` returns the transfer and its status, to its owner only (same rule as ADR-0007).

### 3. Idempotency at two levels

**Client requests.** The `Idempotency-Key` header is **required** on `POST /transfers`.
- The `transfer_id` is derived deterministically from the caller and the key (UUID v5 of `owner_id` + key). The transfer is written with a condition `attribute_not_exists(transfer_id)`.
- The same key with the **same** payload returns the existing transfer (`202`), without starting a second execution.
- The same key with a **different** payload is refused with **`422 Unprocessable Content`**.
- The Step Functions execution is **named** after the `transfer_id`: Step Functions refuses a second execution with the same name, a second safety net.

**Saga steps.** Each step can be replayed safely. Debit, credit and refund each use one **DynamoDB transaction** (`TransactWriteItems`) that updates the balance **and** marks the step as done on the transfer item (`debited_at`, `credited_at`, `refunded_at`), with a condition that the mark does not exist yet. A replayed step finds the mark, changes nothing, and is treated as already successful.

### 4. Business rules

- The caller must **own the source account** (ADR-0007). The target account can belong to any user, but must exist.
- Source and target are different accounts, in the same currency.
- The amount is a strictly positive integer in minor units (cents).
- A debit is conditional on `balance >= amount`: a balance never becomes negative.

### 5. Errors and retries

- **Transient errors** (throttling, Lambda service errors, fraud service unavailable): retried by the state machine with exponential backoff and jitter, a limited number of times.
- **Business errors** (fraud refused, insufficient funds): not retried; they lead to the corresponding final status.
- The fraud service is simulated and intentionally unreliable; a circuit breaker is added in phase 5.

## Rationale

- **Orchestration rather than choreography:** the sequence, the compensation and the status of every transfer are explicit in one place, and visible in the Step Functions console with the history of each execution. Choreography (services reacting to events) is used in phase 3 for side effects such as notifications.
- **Standard rather than Express workflow:** Standard workflows run **exactly once** per execution name, keep a 90-day execution history, and support long waits. Express workflows are cheaper for high volumes but run at least once and keep no built-in history.
- **Deterministic `transfer_id`:** idempotency without an extra table or lookup.
- **DynamoDB transactions:** the balance change and the "step done" mark are atomic, which makes each step idempotent and never leaves a half-applied step.

## Consequences

**Positive**
- No money is lost or created: every path ends in a consistent final status, and the compensation is automatic.
- Retries are safe at every level: client, Step Functions and Lambda.
- Each transfer has an auditable execution history.

**Negative / accepted**
- **Eventual consistency:** between the debit and the credit, the money is "in flight"; the source balance is lower before the target balance is higher. The client sees a `PENDING` status during this time.
- **Asynchronous API:** clients must poll `GET /transfers/{id}` (or, later, receive a notification) to know the outcome.
- **Cost per transfer:** about ten state transitions per transfer (0.025 USD per 1,000 transitions beyond the free tier of 4,000 per month). Negligible at the volume of this project.
- **Compensation is visible:** a `COMPENSATED` transfer shows a debit and a refund on the source account, which is the expected behavior of a Saga (no "rollback" that erases history).
- **No isolation between Sagas:** two transfers from the same account run concurrently; the conditional debit prevents a negative balance, but the second transfer may fail with insufficient funds.

## Alternatives not chosen

- **Single DynamoDB transaction (debit + credit together):** atomic and simpler, but cannot include the external fraud check, and does not demonstrate the Saga pattern required by a multi-service design. It is used **inside** each step instead.
- **Choreography with EventBridge:** looser coupling, but the flow and the compensation would be spread across services and harder to follow.
- **Synchronous API (`201` when done):** simpler for clients, but holds the HTTP request open during retries and is limited by the API Gateway integration timeout (29 seconds by default).
- **Express workflow:** cheaper at scale, but at-least-once execution and no built-in history.
