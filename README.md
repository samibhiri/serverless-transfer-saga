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
  - [x] Repository with protected `main` branch and pull-request workflow
  - [x] Reproducible development environment (dev container)
  - [x] Multi-account setup, IAM Identity Center, MFA, cost budget ([ADR-0002](docs/adr/0002-aws-account-structure-and-access-management.md))
  - [ ] Terraform remote state
- [ ] **Phase 1** — Synchronous API (API Gateway, Lambda, DynamoDB, Cognito), CI/CD
- [ ] **Phase 2** — Transfer Saga with compensation (Step Functions), idempotency
- [ ] **Phase 3** — Event-driven communication (EventBridge, SQS, DLQ, outbox)
- [ ] **Phase 4** — CQRS read model
- [ ] **Phase 5** — Resilience and observability
- [ ] **Phase 6** — Security hardening
- [ ] **Phase 7** — Well-Architected review, cost estimate, final documentation

## Documentation

Architecture Decision Records:

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-use-terraform-for-infrastructure-as-code.md) | Use Terraform for Infrastructure as Code |
| [0002](docs/adr/0002-aws-account-structure-and-access-management.md) | AWS account structure and access management |

## Tech stack

| Area | Choice |
|---|---|
| Cloud | AWS, dedicated workload account in an AWS Organization |
| Access | IAM Identity Center, temporary credentials only |
| Infrastructure as Code | Terraform |
| CI/CD | GitHub Actions |
| Dev environment | GitHub Codespaces (dev container) |

## Getting started

Open the repository in GitHub Codespaces: the dev container provides Python, Terraform, TFLint and the AWS CLI.

Then sign in to AWS with IAM Identity Center (no access keys are used):

```bash
# First time only: create a CLI profile for the workload account
aws configure sso --use-device-code

# When the session has expired
aws sso login --profile <your-profile> --use-device-code

# Check which identity is in use
aws sts get-caller-identity --profile <your-profile>
```

## License

[MIT](LICENSE)
