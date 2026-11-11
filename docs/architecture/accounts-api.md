# Accounts API

This view shows how a request to the accounts API is processed.
Decisions: [ADR-0004](../adr/0004-one-lambda-function-per-service.md) (one function per service), [ADR-0005](../adr/0005-api-gateway-rest-api.md) (API Gateway REST API).

> Authentication with Amazon Cognito is added in the next step of phase 1.

## Components

```mermaid
flowchart LR
    client(["API client"])

    subgraph wl["Workload account: transfer (eu-west-3)"]
        subgraph api["API Gateway REST API, stage dev"]
            post["POST /accounts"]
            get["GET /accounts/{account_id}"]
        end
        fn["Lambda: transfer-saga-dev-accounts<br/>Python 3.13, arm64, 256 MB, 10 s"]
        role["IAM execution role<br/>GetItem + PutItem on the table<br/>write to its own log group"]
        ddb[("DynamoDB: accounts<br/>on-demand, PITR")]
        alog["Access logs<br/>/aws/apigateway/.../access"]
        flog["Function logs (JSON)<br/>/aws/lambda/transfer-saga-dev-accounts"]
    end

    client -- "HTTPS" --> post
    client -- "HTTPS" --> get
    post -- "Lambda proxy" --> fn
    get -- "Lambda proxy" --> fn
    fn -. "assumes" .- role
    fn --> ddb
    api --> alog
    fn --> flog

    classDef actor fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef integration fill:#FBEAF0,stroke:#D4537E,color:#72243E
    classDef compute fill:#FAECE7,stroke:#D85A30,color:#712B13
    classDef data fill:#EEEDFE,stroke:#7F77DD,color:#3C3489
    classDef identity fill:#FCEBEB,stroke:#E24B4A,color:#791F1F
    class client actor
    class post,get,alog,flog integration
    class fn compute
    class ddb data
    class role identity
```

## Request flow: create an account

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API Gateway
    participant L as Lambda (accounts)
    participant D as DynamoDB (accounts)

    C->>A: POST /accounts {"owner": "Alice", "initial_balance": 10000}
    A->>A: throttling check (10 req/s, burst 20)
    A->>L: invoke with the request as an event (Lambda proxy)
    L->>L: validate input (owner, amount in cents, currency)
    alt invalid input
        L-->>A: 400 + message
    else valid input
        L->>D: PutItem, condition attribute_not_exists(account_id)
        D-->>L: OK
        L-->>A: 201 + account
    end
    A-->>C: HTTP response
    A->>A: write one access log line
```

## Routes

| Method and path | Success | Errors |
|---|---|---|
| `POST /accounts` | `201` with the new account | `400` invalid input, `429` throttled, `500` internal error |
| `GET /accounts/{account_id}` | `200` with the account | `400` invalid id, `404` unknown account, `429` throttled, `500` internal error |

Amounts are integers in minor units (cents). Internal errors return a generic message; details are written to the function logs only.

## Security and operations

| Concern | Implementation |
|---|---|
| Least privilege | The function role allows only `GetItem` and `PutItem` on the accounts table, and writing to its own log group. API Gateway may invoke the function only for these two routes of this API. |
| Rate limiting | Stage throttling on every method: 10 requests per second, bursts of 20. Excess requests receive `429 Too Many Requests`. |
| Data integrity | Conditional write: an existing account is never overwritten. Amounts are integers, never floats. |
| Logs | JSON access logs (one line per request) and JSON function logs, both kept 14 days. |
| Metrics | API Gateway method metrics (count, latency, 4xx, 5xx) and Lambda metrics in CloudWatch. |
| Backups | DynamoDB point-in-time recovery (35 days). |
