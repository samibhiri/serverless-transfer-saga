# Transfer Saga

> **Design of phase 2, being implemented.** This view describes the target design; it is updated as each part is built.
>
> | Part | Status |
> |---|---|
> | `transfers` table | built |
> | Transfers API code (`services/transfers`), idempotency, unit tests | built |
> | Saga steps: fraud check, debit, credit, refund | to do |
> | Step Functions state machine | to do |
> | API routes and Lambda function of the transfers API | to do |

This view shows how a transfer between two accounts is processed.
Decision: [ADR-0008](../adr/0008-transfer-saga-orchestrated-with-step-functions.md) (Saga orchestrated with Step Functions). Authentication and authorization follow [ADR-0006](../adr/0006-api-authentication-with-cognito.md) and [ADR-0007](../adr/0007-authorization-by-account-ownership.md).

## Components

```mermaid
flowchart LR
    client(["API client"])

    subgraph wl["Workload account: transfer (eu-west-3)"]
        apigw["API Gateway<br/>POST /transfers<br/>GET /transfers/{id}"]
        api["Lambda: transfers API<br/>validation, idempotency"]
        sfn["Step Functions<br/>transfer Saga (Standard)"]
        steps["Lambda: Saga steps<br/>debit, credit, refund"]
        fraud["Lambda: fraud check<br/>simulated, unreliable"]
        transfers[("DynamoDB: transfers<br/>status, step marks")]
        accounts[("DynamoDB: accounts<br/>balances")]
    end

    client -- "HTTPS + ID token" --> apigw
    apigw --> api
    api -- "PENDING" --> transfers
    api -- "start execution<br/>name = transfer_id" --> sfn
    sfn --> fraud
    sfn --> steps
    steps -- "transactions" --> accounts
    steps -- "transactions" --> transfers
    sfn -- "final status" --> transfers

    classDef actor fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef integration fill:#FBEAF0,stroke:#D4537E,color:#72243E
    classDef compute fill:#FAECE7,stroke:#D85A30,color:#712B13
    classDef data fill:#EEEDFE,stroke:#7F77DD,color:#3C3489
    class client actor
    class apigw,sfn integration
    class api,steps,fraud compute
    class transfers,accounts data
```

## State machine

```mermaid
flowchart TB
    start(["Execution started<br/>transfer PENDING"])
    fraud{"Fraud check"}
    debit{"Debit source<br/>if balance >= amount"}
    credit{"Credit target"}
    refund["Refund source<br/>(compensation)"]
    ok(["COMPLETED"])
    rej(["REJECTED<br/>no movement"])
    fail(["FAILED<br/>no movement"])
    comp(["COMPENSATED<br/>source refunded"])

    start --> fraud
    fraud -- "accepted" --> debit
    fraud -- "refused" --> rej
    debit -- "done" --> credit
    debit -- "insufficient funds" --> fail
    credit -- "done" --> ok
    credit -- "failed after retries" --> refund --> comp

    classDef neutral fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef step fill:#FBEAF0,stroke:#D4537E,color:#72243E
    classDef compensation fill:#FAEEDA,stroke:#BA7517,color:#633806
    classDef success fill:#EAF3DE,stroke:#3B6D11,color:#27500A
    classDef failure fill:#FCEBEB,stroke:#A32D2D,color:#791F1F
    class start neutral
    class fraud,debit,credit step
    class refund,comp compensation
    class ok success
    class rej,fail failure
```

Transient errors (throttling, service errors, fraud service unavailable) are retried by Step Functions with exponential backoff and jitter. Business errors (fraud refused, insufficient funds) are not retried.

## Request flow: create a transfer

```mermaid
sequenceDiagram
    participant C as Client (Alice)
    participant A as API Gateway
    participant T as Lambda (transfers API)
    participant D as DynamoDB (transfers)
    participant S as Step Functions

    C->>A: POST /transfers, Idempotency-Key: k1, ID token
    A->>T: request + claims (sub = Alice)
    T->>T: validate input, check Alice owns the source account
    T->>T: transfer_id = UUIDv5(Alice, k1)
    T->>D: PutItem PENDING, condition attribute_not_exists(transfer_id)
    alt new transfer
        D-->>T: OK
        T->>S: StartExecution(name = transfer_id)
        T-->>C: 202 Accepted + transfer_id
    else key already used, same payload
        D-->>T: condition failed
        T-->>C: 202 Accepted + existing transfer (no new execution)
    else key already used, different payload
        D-->>T: condition failed
        T-->>C: 422 Unprocessable Content
    end
    loop until a final status
        C->>A: GET /transfers/{transfer_id}
        A-->>C: 200 + status
    end
```

## Idempotent steps

Each money movement is one DynamoDB transaction that changes the balance **and** marks the step as done on the transfer. If Step Functions retries a step that had in fact succeeded, the mark already exists, the transaction is cancelled without any change, and the step reports success.

```mermaid
sequenceDiagram
    participant S as Step Functions
    participant L as Lambda (debit step)
    participant DB as DynamoDB

    S->>L: debit(transfer_id)
    L->>DB: TransactWriteItems: (1) accounts: balance -= amount, if balance >= amount, (2) transfers: set debited_at, if not set
    alt first attempt
        DB-->>L: committed
        L-->>S: done
    else retry after a lost response
        DB-->>L: cancelled: debited_at already set
        L-->>S: done (no second debit)
    else insufficient funds
        DB-->>L: cancelled: balance condition failed
        L-->>S: InsufficientFunds (business error, not retried)
    end
```

## Data model

**`transfers` table**, partition key `transfer_id`:

| Attribute | Description |
|---|---|
| `transfer_id` | UUID v5 of the owner and the idempotency key |
| `owner_id` | Cognito `sub` of the caller (owner of the source account) |
| `source_account_id`, `target_account_id` | accounts involved |
| `amount`, `currency` | integer amount in minor units, currency of both accounts |
| `status` | `PENDING`, `COMPLETED`, `REJECTED`, `FAILED`, `COMPENSATED` |
| `debited_at`, `credited_at`, `refunded_at` | step marks, set by the step transactions (idempotency) |
| `failure_reason` | for `REJECTED`, `FAILED` and `COMPENSATED` |
| `created_at`, `updated_at` | timestamps |

## Routes

| Method and path | Success | Errors |
|---|---|---|
| `POST /transfers` | `202` with the transfer (new, or existing for the same idempotency key) | `400` invalid input or missing `Idempotency-Key`, `401` / `403` authentication, `404` source account not found or not owned, `422` key reused with a different payload, `429` throttled |
| `GET /transfers/{transfer_id}` | `200` with the transfer and its status, if the caller owns it | `400` invalid id, `401` / `403` authentication, `404` unknown transfer or transfer of another user |

## Implementation notes

- **Idempotency key:** required header `Idempotency-Key` (case-insensitive name), 1 to 255 printable characters. `transfer_id = UUIDv5(project namespace, owner_id + ":" + key)`: two users can use the same key without collision.
- **Replay:** a request with an already used key is compared on `source_account_id`, `target_account_id` and `amount`. Same values: `202` with the existing transfer. Different values: `422`.
- **Self-healing start:** if a previous attempt recorded the transfer but failed to start the Saga, a replay with the same key starts it. The execution name (`transfer_id`) prevents a second execution.
- **Race between two identical requests:** the conditional write lets only one of them create the transfer; the other one reads it back and answers as a replay.
- **Checks before recording a transfer:** the caller owns the source account (`404` otherwise, same answer as an unknown account), the target account exists (`404`), both accounts have the same currency (`400`). The balance is **not** checked here: the debit step of the Saga does it atomically.
