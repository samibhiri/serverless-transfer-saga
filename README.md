# Serverless Transfer Saga on AWS

> 🚧 **Work in progress** — this project is built step by step. See the [roadmap](#roadmap).

An event-driven, serverless money-transfer system on AWS, built as a portfolio project to demonstrate cloud architecture practices: distributed transactions (Saga), event-driven design, resilience, security and cost awareness.

> ⚠️ **Simulation only.** This project uses fictitious accounts and data. It does not process real payments.

## Goals

- Design a distributed transaction with compensation (Saga pattern) using managed AWS services.
- Apply event-driven patterns: pub/sub, queues with dead-letter handling, outbox, CQRS.
- Build in resilience (timeouts, retries, circuit breaker, bulkheads, throttling) and observability.
- Follow security best practices: least privilege, encryption, no long-lived credentials.
- Document every significant decision as an Architecture Decision Record (ADR).
- Review the final architecture against the six pillars of the AWS Well-Architected Framework.

## Functional scope

A user transfers money between two fictitious accounts:

1. A fraud check is performed (simulated, intentionally unreliable external service).
2. The source account is debited, then the target account is credited.
3. If a step fails, previous steps are compensated.
4. The user is notified, and can consult the transaction history.

## Architecture

*Diagrams will be added as the project progresses.*

## Roadmap

- [ ] **Phase 0** — Repository, AWS account security, Terraform foundations
- [ ] **Phase 1** — Synchronous API (API Gateway, Lambda, DynamoDB, Cognito), CI/CD
- [ ] **Phase 2** — Transfer Saga with compensation (Step Functions), idempotency
- [ ] **Phase 3** — Event-driven communication (EventBridge, SQS, DLQ, outbox)
- [ ] **Phase 4** — CQRS read model
- [ ] **Phase 5** — Resilience and observability
- [ ] **Phase 6** — Security hardening
- [ ] **Phase 7** — Well-Architected review, cost estimate, final documentation

## Documentation

- [Architecture Decision Records](docs/adr/)

## Tech stack

| Area | Choice |
|---|---|
| Cloud | AWS |
| Infrastructure as Code | Terraform ([ADR-0001](docs/adr/0001-use-terraform-for-infrastructure-as-code.md)) |
| CI/CD | GitHub Actions |

## License

[MIT](LICENSE)
