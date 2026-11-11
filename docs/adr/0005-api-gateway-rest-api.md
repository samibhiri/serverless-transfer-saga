# ADR-0005: Amazon API Gateway REST API

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The project exposes a public HTTPS API in front of its Lambda functions. Amazon API Gateway offers two products for this:

- **REST API** (API Gateway v1): the full-featured product;
- **HTTP API** (API Gateway v2): simpler, faster and cheaper, with fewer features.

The API must support authentication with Amazon Cognito, rate limiting, access logs, and later protection with AWS WAF.

## Decision

We use an **API Gateway REST API**, with a **regional** endpoint and **Lambda proxy integrations**.

- One stage per environment (`dev`), with **JSON access logs** in CloudWatch Logs (14-day retention).
- **Throttling** on every method of the stage: 10 requests per second, bursts of 20 (configurable).
- Method **metrics** enabled; execution logs at `ERROR` level only.
- **Lambda permissions** are scoped to the exact method and path of this API.

## Rationale

| Need | REST API | HTTP API |
|---|---|---|
| Cognito authentication | Cognito user pool authorizer | JWT authorizer |
| Rate limiting per stage or method | yes | yes |
| **Usage plans and API keys** (quotas per client) | **yes** | no |
| **AWS WAF** integration | **yes** | no |
| Request validation (models) | yes | no |
| Caching, private endpoints, resource policies | yes | no |
| Price | ~3.50 USD per million requests | ~1.00 USD per million requests |
| Latency | higher | lower |

The deciding features are **AWS WAF** (planned in the security phase) and **usage plans** (rate limiting per client). At the traffic of this project, the price difference is negligible.

## Consequences

**Positive**
- WAF, usage plans and request validation are available when needed, without migrating.
- Access logs give a per-request audit trail (source IP, path, status, latency).

**Negative / accepted**
- Higher cost per request and slightly higher latency than an HTTP API; significant only at high volume.
- More verbose configuration (resources, methods, integrations, deployment and stage are separate objects).
- A new **deployment** must be created when the API changes; Terraform does it automatically through a hash of the API definition.
- The CloudWatch Logs role of API Gateway is an **account-level** setting, managed in the `dev` configuration because the workload account hosts a single environment.

## Alternatives not chosen

- **HTTP API:** a good default for simple APIs; rejected here because it supports neither AWS WAF nor usage plans.
- **Lambda function URLs:** no authorizers, throttling or WAF; suited to internal or webhook endpoints only.
- **Application Load Balancer:** hourly cost even without traffic, and no native API features.
