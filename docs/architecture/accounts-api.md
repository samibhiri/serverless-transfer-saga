# Accounts API

This view shows how a request to the accounts API is processed.
Decisions: [ADR-0004](../adr/0004-one-lambda-function-per-service.md) (one function per service), [ADR-0005](../adr/0005-api-gateway-rest-api.md) (API Gateway REST API), [ADR-0006](../adr/0006-api-authentication-with-cognito.md) (authentication with Cognito), [ADR-0007](../adr/0007-authorization-by-account-ownership.md) (authorization by account ownership).

## Components

```mermaid
flowchart LR
    client(["API client"])

    subgraph wl["Workload account: transfer (eu-west-3)"]
        cognito["Cognito user pool<br/>admin-created users, email sign-in"]
        subgraph api["API Gateway REST API, stage dev"]
            authz["Cognito authorizer<br/>validates the ID token"]
            post["POST /accounts"]
            get["GET /accounts/{account_id}"]
        end
        fn["Lambda: transfer-saga-dev-accounts<br/>Python 3.13, arm64, 256 MB, 10 s"]
        role["IAM execution role<br/>GetItem + PutItem on the table<br/>write to its own log group"]
        ddb[("DynamoDB: accounts<br/>on-demand, PITR")]
        alog["Access logs<br/>/aws/apigateway/.../access"]
        flog["Function logs (JSON)<br/>/aws/lambda/transfer-saga-dev-accounts"]
    end

    client -- "1. sign in" --> cognito
    client -- "2. HTTPS + ID token" --> authz
    authz -. "keys, issuer" .-> cognito
    authz --> post
    authz --> get
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
    class post,get,authz,alog,flog integration
    class fn compute
    class ddb data
    class role,cognito identity
```

## Request flow: create an account

```mermaid
sequenceDiagram
    participant C as Client
    participant U as Cognito user pool
    participant A as API Gateway
    participant L as Lambda (accounts)
    participant D as DynamoDB (accounts)

    C->>U: sign in (email + password)
    U-->>C: ID token (1 hour) + refresh token
    C->>A: POST /accounts, Authorization: ID token
    A->>A: throttling check (10 req/s, burst 20)
    A->>A: verify token signature, issuer and expiry
    alt missing or invalid token
        A-->>C: 401 Unauthorized (Lambda is not invoked)
    else valid token
        A->>L: invoke with the request and the token claims (Lambda proxy)
        L->>L: owner_id = sub claim of the caller (never from the body)
        L->>L: validate input (owner name, amount in cents, currency)
        alt invalid input
            L-->>A: 400 + message
        else valid input
            L->>D: PutItem, condition attribute_not_exists(account_id)
            D-->>L: OK
            L-->>A: 201 + account
        end
        A-->>C: HTTP response
    end
    A->>A: write one access log line
```

## Request flow: read an account

Each user can only read their own accounts. An account that belongs to someone else gets exactly the same answer as an account that does not exist.

```mermaid
sequenceDiagram
    participant C as Client (signed in as Bob)
    participant A as API Gateway
    participant L as Lambda (accounts)
    participant D as DynamoDB (accounts)

    C->>A: GET /accounts/{account_id}, Authorization: Bob's ID token
    A->>A: verify token
    A->>L: invoke with the request and the claims (sub = Bob)
    L->>D: GetItem(account_id)
    D-->>L: item, or nothing
    alt no item, or item.owner_id differs from Bob
        L-->>A: 404 Account not found
    else item.owner_id is Bob
        L-->>A: 200 + account
    end
    A-->>C: HTTP response
```

## Routes

| Method and path | Success | Errors |
|---|---|---|
| `POST /accounts` | `201` with the new account | `400` invalid input, `401` missing or invalid token, `429` throttled, `500` internal error |
| `GET /accounts/{account_id}` | `200` with the account, if the caller owns it | `400` invalid id, `401` missing or invalid token, `404` unknown account **or account of another user**, `429` throttled, `500` internal error |

Amounts are integers in minor units (cents). Internal errors return a generic message; details are written to the function logs only.

## Security and operations

| Concern | Implementation |
|---|---|
| Authentication | Cognito user pool, admin-created users only, strong password policy, optional TOTP MFA. API Gateway rejects requests without a valid ID token (`401`) before invoking Lambda. |
| Authorization | An account belongs to the user who created it (`owner_id` = Cognito `sub`, taken from the verified token). Users can only read their own accounts; other accounts return `404`, like unknown ones. Requests without a verified identity are refused (`401`) by the function too (defense in depth). |
| Least privilege | The function role allows only `GetItem` and `PutItem` on the accounts table, and writing to its own log group. API Gateway may invoke the function only for these two routes of this API. |
| Rate limiting | Stage throttling on every method: 10 requests per second, bursts of 20. Excess requests receive `429 Too Many Requests`. |
| Data integrity | Conditional write: an existing account is never overwritten. Amounts are integers, never floats. |
| Logs | JSON access logs (one line per request) and JSON function logs, both kept 14 days. |
| Metrics | API Gateway method metrics (count, latency, 4xx, 5xx) and Lambda metrics in CloudWatch. |
| Backups | DynamoDB point-in-time recovery (35 days). |
