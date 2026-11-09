# ADR-0003: Terraform remote state and pipeline authentication

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The infrastructure is deployed through GitHub Actions:

- on every **pull request**, a workflow runs `terraform plan` and shows the result for review;
- a **manually triggered** workflow runs `terraform apply`.

For this, the pipeline needs:

1. a **shared, durable Terraform state**, readable by both the pipeline and the developer;
2. a way to **authenticate to AWS without long-lived access keys** stored in GitHub;
3. **different permissions** for planning (read) and deploying (write).

These resources cannot be created by the pipeline itself, since the pipeline depends on them.

## Decision

### 1. A bootstrap configuration, applied once by hand

A small Terraform configuration (`infra/bootstrap`) creates the foundations of the pipeline. It is applied **once, manually**, from the development environment with an IAM Identity Center session. Everything else is deployed by the pipeline.

### 2. Remote state in Amazon S3

- One S3 bucket in the workload account stores the state of every Terraform configuration, each under its own key (for example `bootstrap/terraform.tfstate`).
- The bucket is **private** (public access blocked, ACLs disabled), **versioned**, **encrypted at rest** (SSE-S3) and **accessible only over TLS** (bucket policy).
- Old state versions expire after 90 days; the bucket is protected against deletion (`prevent_destroy`).
- **State locking uses S3 native locking** (`use_lockfile`); no DynamoDB table is needed.
- The bootstrap state itself is migrated into this bucket after its first apply.
- The bucket name contains the account ID; it is passed at `terraform init` time (partial backend configuration), so that account IDs are not published in the repository.

### 3. GitHub Actions authenticates with OpenID Connect

- An IAM **OIDC identity provider** trusts tokens issued by GitHub Actions.
- Workflows exchange their OIDC token for **temporary credentials** (AWS STS `AssumeRoleWithWebIdentity`, one-hour sessions). No AWS access keys exist in GitHub.
- Trust conditions use GitHub's **immutable subject claim** format, which includes the numeric IDs of the owner and of the repository: `repo:OWNER@OWNER-ID/REPO@REPO-ID:<context>`. This format is used by default for repositories created after 15 July 2026. Unlike names, numeric IDs are never reused, so a repository deleted and recreated under the same name by someone else cannot assume the roles.

### 4. Two roles with distinct trust and permissions

| Role | Who can assume it (`sub` claim) | Permissions |
|---|---|---|
| `github-actions-terraform-plan` | workflows triggered by a **pull request** of this repository (`…:pull_request`) | `ReadOnlyAccess` + create/delete the state lock file |
| `github-actions-terraform-deploy` | jobs running in the **GitHub environment `dev`** of this repository (`…:environment:dev`) | `AdministratorAccess` (temporary, see below) |

The GitHub environment `dev` is restricted to the `main` branch, so that only reviewed and merged code can be deployed.

## Rationale

- **No long-lived credentials**, in line with the IAM best practices and ADR-0002.
- **Least privilege by context:** code in a pull request is not yet reviewed, so it only gets read access. Write access requires the protected deployment environment.
- **Trust conditions are evaluated by AWS**, from claims signed by GitHub: a workflow cannot claim another context.
- A single, versioned state bucket gives a shared source of truth and a recovery path from a corrupted state.

## Consequences

**Positive**
- The pipeline can plan and deploy without any secret stored in GitHub.
- Each Terraform configuration has its own state and lock, which limits the blast radius of an error.

**Negative / accepted risks**
- **The bootstrap is managed outside the pipeline.** Changes to it are rare and applied manually.
- **The deploy role has administrator permissions.** The application creates IAM roles for its functions, which requires broad IAM permissions. To be narrowed in the security phase (for example with a permissions boundary).
- **`ReadOnlyAccess` is broad** for a plan role: it can read most resource configurations of the account. Accepted for a workload account that contains only this project.
- **Public repository:** pull requests from forks do not receive OIDC tokens with access to these roles, but workflow changes inside the repository must be reviewed carefully.

## Alternatives not chosen

- **Access keys stored as GitHub secrets:** long-lived, hard to rotate, a frequent cause of leaks.
- **DynamoDB table for state locking:** no longer required since S3 supports native locking; one less resource to manage.
- **Terraform Cloud / HCP Terraform for state:** an additional external service and account, not needed here.
- **A single role for plan and apply:** simpler, but gives write access to unreviewed code.
- **Encryption with a customer managed KMS key:** additional cost (about 1 USD per month) with no benefit for this threat model; SSE-S3 is sufficient.
