# ADR-0004: One Lambda function per service

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The accounts API exposes several routes behind Amazon API Gateway (`POST /accounts`, `GET /accounts/{account_id}`). The Lambda code can be organized in two ways:

- **one function per service**: a single function receives every route of the service and routes requests in code;
- **one function per route**: API Gateway routes each request to a dedicated function, with its own IAM role.

## Decision

We deploy **one Lambda function per service**. The accounts service is one function (`services/accounts/app.py`, entry point `app.handler`) that routes on the HTTP method and the API Gateway resource path.

Its IAM role is still scoped to the minimum for the whole service: only `dynamodb:PutItem` and `dynamodb:GetItem`, only on the accounts table.

## Rationale

- **Simplicity:** one function, one role and one log group per service; less infrastructure code to write and review.
- **Shared code without packaging concerns:** validation and helpers live in one module, deployed once.
- **Fewer cold starts:** all routes of the service share warm execution environments.
- The service is small and its routes have similar needs (memory, timeout).

## Consequences

**Positive**
- Fewer AWS resources and a smaller Terraform configuration.
- A single place to read the behavior of the service.

**Negative / accepted risks**
- **Coarser least privilege:** the read route runs with write permission on the accounts table. A bug or a compromise in the read path could write data. Accepted given the size of the service; revisited in the security phase.
- **Shared configuration:** memory, timeout and concurrency limits apply to every route of the service.
- **Mixed observability:** metrics of the function cover all routes; per-route analysis relies on API Gateway metrics and structured logs.
- **Routing in code** must stay consistent with the routes declared in API Gateway (covered by unit tests).

## Alternatives not chosen

- **One function per route:** finer least privilege (a read function cannot write) and per-route settings, at the cost of more resources and cold starts. A good option if the service grows or if routes need different permissions or limits.
- **A web framework adapter** (for example, the Powertools event handler): convenient for larger APIs; not needed for two routes.
