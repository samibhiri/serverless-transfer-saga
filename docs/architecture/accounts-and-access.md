# Accounts and access

This view shows how the AWS accounts are organized and how humans and the CI/CD pipeline access them.
Decisions: [ADR-0002](../adr/0002-aws-account-structure-and-access-management.md), [ADR-0003](../adr/0003-terraform-state-and-pipeline-authentication.md).

```mermaid
flowchart TB
    dev(["Developer<br/>password + MFA"])
    gh(["GitHub Actions<br/>OIDC token"])

    subgraph org["AWS Organization"]
        subgraph mgmt["Management account: identity, billing, no workloads"]
            idc["IAM Identity Center<br/>eu-west-3"]
            budget["AWS Budgets<br/>monthly cost alerts"]
            breakglass["IAM user + MFA<br/>break-glass access"]
        end
        subgraph wl["Workload account: transfer"]
            sso["AWSReservedSSO_AdministratorAccess<br/>role, 4 h sessions"]
            orgrole["OrganizationAccountAccessRole<br/>role"]
            plan["github-actions-terraform-plan<br/>role, read-only"]
            deploy["github-actions-terraform-deploy<br/>role, administrator"]
        end
    end

    dev -- "access portal or aws sso login" --> idc
    idc -- "temporary credentials" --> sso
    breakglass -. "emergency only" .-> orgrole
    gh -- "sub = …:pull_request" --> plan
    gh -- "sub = …:environment:dev" --> deploy

    classDef actor fill:#F1EFE8,stroke:#888780,color:#2C2C2A
    classDef identity fill:#FCEBEB,stroke:#E24B4A,color:#791F1F
    classDef mgmtsvc fill:#FBEAF0,stroke:#D4537E,color:#72243E
    class dev,gh actor
    class idc,sso,orgrole,plan,deploy,breakglass identity
    class budget mgmtsvc
```

## Who can access what

| Identity | Authenticates with | Role assumed | Account | Permissions |
|---|---|---|---|---|
| Developer | IAM Identity Center (portal or `aws sso login`), MFA | `AWSReservedSSO_AdministratorAccess_…` | management or `transfer` | Administrator |
| CI on a pull request | GitHub OIDC token, `sub` ending in `:pull_request` | `github-actions-terraform-plan` | `transfer` | Read-only + state lock |
| CD in environment `dev` | GitHub OIDC token, `sub` ending in `:environment:dev` | `github-actions-terraform-deploy` | `transfer` | Administrator |
| Break-glass | IAM user with MFA in the management account | `OrganizationAccountAccessRole` | `transfer` | Administrator |

No identity uses long-lived access keys: every access goes through a role and temporary credentials.
