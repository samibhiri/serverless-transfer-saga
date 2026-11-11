# CI/CD

This view shows how infrastructure changes go from a pull request to AWS.
Decision: [ADR-0003](../adr/0003-terraform-state-and-pipeline-authentication.md).

## Delivery flow

```mermaid
flowchart LR
    pr["Pull request<br/>to main"]
    lint["Job: Lint<br/>fmt, validate, TFLint<br/>no AWS access"]
    plan["Job: Plan (dev)<br/>terraform plan"]
    review{"Review +<br/>required checks"}
    main["main branch"]
    approve{"Manual run +<br/>approval (dev)"}
    deploy["Job: Deploy (dev)<br/>terraform plan + apply"]

    subgraph aws["AWS workload account: transfer"]
        sts["AWS STS"]
        state[("S3 bucket<br/>Terraform state")]
        app["Application resources<br/>infra/envs/dev"]
    end

    pr --> lint --> plan
    plan -- "OIDC: plan role" --> sts
    plan -- "read state, lock" --> state
    plan -- "plan posted as a comment" --> pr
    pr --> review -- "squash and merge" --> main
    main --> approve --> deploy
    deploy -- "OIDC: deploy role" --> sts
    deploy -- "read and write state" --> state
    deploy -- "apply" --> app

    classDef gh fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef awssvc fill:#FBEAF0,stroke:#D4537E,color:#72243E
    classDef data fill:#EEEDFE,stroke:#7F77DD,color:#3C3489
    class pr,lint,plan,review,main,approve,deploy gh
    class sts awssvc
    class state,app data
```

## Workflows

| Workflow | Trigger | Job | AWS role | What it does |
|---|---|---|---|---|
| `terraform-plan.yml` | every pull request to `main` | Lint | none | `fmt`, `validate` and TFLint on every configuration |
| | | Plan (dev) | plan role (read-only) | `terraform plan`, posted as a pull-request comment |
| `tests.yml` | every pull request to `main` | Unit tests | none | `pytest` on the Lambda code, AWS mocked with moto |
| `terraform-deploy.yml` | manual (`workflow_dispatch`), from `main`, action `apply` | Deploy (dev) | deploy role | `terraform plan`, shown in the job summary, then `terraform apply` of that exact plan |
| | same workflow, action `destroy` | Destroy (dev) | deploy role | `terraform plan -destroy`, shown in the job summary, then applied: every resource of `infra/envs/dev` is deleted; the bootstrap is not affected |

To save costs, the `dev` application is destroyed between working sessions with the `destroy` action, and redeployed with `apply`. Both go through the same approval and leave a run in the history of the repository.

The reviewer approves the deployment before the job computes its plan. This is accepted because the same code was already planned and reviewed in its pull request. Separating plan and apply into two jobs, with the approval between them, would require an additional role and environment.

## Authentication of a pull-request workflow

```mermaid
sequenceDiagram
    participant W as Workflow job (pull request)
    participant G as GitHub OIDC provider
    participant S as AWS STS
    participant B as S3 state bucket

    W->>G: request an ID token (audience sts.amazonaws.com)
    G-->>W: signed token, sub = repo:OWNER@ID/REPO@ID:pull_request
    W->>S: AssumeRoleWithWebIdentity (plan role, token)
    S->>S: verify signature, then aud and sub against the trust policy
    S-->>W: temporary credentials (1 hour)
    W->>B: terraform init / plan (read state, write lock file)
```

## Repository protection

- `main` is protected by a ruleset: no direct push, no force push, linear history.
- Every change goes through a pull request, merged with **squash and merge**.
- The **Lint**, **Plan (dev)** and **Unit tests** checks must pass, on a branch up to date with `main`.
- Pull requests from forks do not run the plan job: they receive no secrets and cannot assume the AWS role.

## Deployment protection

The GitHub environment `dev` is the second lock on the deploy role, in addition to the `sub` condition of its trust policy:

- **Deployment branches:** only `main`, so only reviewed and merged code can be deployed.
- **Required reviewer:** every deployment waits for a manual approval.
- **Environment secret:** the deploy role ARN is readable only by jobs running in this environment.
- **Concurrency:** one deployment at a time; a deployment in progress is never cancelled.
