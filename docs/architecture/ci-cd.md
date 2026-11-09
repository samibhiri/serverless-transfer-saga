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
    deploy["Deploy workflow<br/>manual, environment dev<br/>terraform apply"]

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
    main -. "planned" .-> deploy
    deploy -. "OIDC: deploy role" .-> sts
    deploy -. "apply" .-> app

    classDef gh fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef awssvc fill:#FBEAF0,stroke:#D4537E,color:#72243E
    classDef data fill:#EEEDFE,stroke:#7F77DD,color:#3C3489
    classDef todo fill:#FFFFFF,stroke:#888780,color:#5F5E5A,stroke-dasharray: 4 3
    class pr,lint,plan,review,main gh
    class sts awssvc
    class state,app data
    class deploy todo
```

Dashed elements are not built yet.

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
- The **Lint** and **Plan (dev)** checks must pass, on a branch up to date with `main`.
- Pull requests from forks do not run the plan job: they receive no secrets and cannot assume the AWS role.
