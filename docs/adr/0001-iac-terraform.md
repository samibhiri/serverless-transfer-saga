# ADR-0001: Use Terraform for Infrastructure as Code

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

All AWS infrastructure of this project must be defined as code, so that it is reviewable through pull requests, deployable from a CI/CD pipeline, reproducible, and fully destroyable to control costs.

Several tools are available for AWS:

- **AWS CloudFormation** — native AWS service, managed state, change sets and drift detection.
- **AWS SAM** — CloudFormation extension specialised for serverless, with local testing tools.
- **AWS CDK** — infrastructure defined in a general-purpose language, synthesised to CloudFormation.
- **Terraform** — declarative, multi-cloud, large ecosystem; state managed by the user.
- **OpenTofu** — open-source fork of Terraform, largely compatible.

## Decision

We use **Terraform** with the AWS provider.

## Rationale

- Prior hands-on experience with Terraform: effort goes into architecture rather than learning a new tool.
- Widely adopted in the industry, including in AWS-centric organisations and multi-cloud contexts.
- Full coverage of the services planned for this project (Lambda, Step Functions, EventBridge, SQS, DynamoDB, Cognito, API Gateway).
- `terraform plan` output fits naturally into pull-request reviews.

## Consequences

**Positive**
- A single, consistent tool for the whole infrastructure.
- Plans can be reviewed in pull requests before any change is applied.

**Negative / to manage**
- Terraform state must be stored and protected by us: remote state in an encrypted, versioned S3 bucket with state locking.
- No native integration with CloudFormation features (drift detection, stack rollback); drift is detected through `terraform plan`.
- SAM's local Lambda emulation is not used; functions are tested with unit tests and in a deployed `dev` environment.

## Alternatives not chosen

- **CloudFormation / SAM:** native and well integrated, but more verbose, and AWS-only. Their concepts (stacks, change sets, drift detection) remain relevant knowledge for AWS architecture.
- **CDK:** powerful, but adds a programming abstraction layer that is not needed for a project of this size.
- **OpenTofu:** a credible option; Terraform is kept for its wider presence in job requirements. Switching later would require little effort.
- **Mixing tools:** rejected; two tools for one infrastructure adds complexity without benefit here.
