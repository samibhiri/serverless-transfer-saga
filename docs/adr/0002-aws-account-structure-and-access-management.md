# ADR-0002: AWS account structure and access management

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The project needs an AWS environment to deploy into. It must be:

- **isolated** from other personal workloads, so that it can be secured, monitored and cleaned up independently;
- **accessed without long-lived credentials**, both from the console and from the development environment (GitHub Codespaces);
- **protected against unexpected costs**.

The starting point was a single, existing AWS account, accessed through an IAM user with administrator permissions.

## Decision

### 1. Multi-account structure with AWS Organizations

```
AWS Organization
└── Root
    ├── Management account   (organization, identity, billing — no workloads)
    └── Workload account "transfer"   (all resources of this project)
```

- The existing account becomes the **management account**. Following AWS guidance, no project resources are deployed in it.
- A dedicated **workload account** (`transfer`) is created through AWS Organizations. It has no root password and no IAM users.
- Billing is **consolidated**: the management account pays for all member accounts.

### 2. Human access through IAM Identity Center

- IAM Identity Center is enabled as an **organization instance**, in **eu-west-3 (Paris)**, with its built-in directory as identity source.
- Access is granted to **groups, not individual users**: the `Administrators` group is assigned the `AdministratorAccess` permission set on each account.
- Sessions obtained through the permission set last **4 hours** (temporary credentials from AWS STS).
- **MFA is mandatory**: users must register a device at first sign-in (passkeys or authenticator apps); MFA is prompted when the sign-in context changes.
- From the development environment, the AWS CLI uses `aws sso login` (device authorization flow). **No access keys are created or stored.**

### 3. Break-glass access

If IAM Identity Center becomes unavailable:

- an **IAM user with MFA** in the management account remains available as an emergency access;
- the **`OrganizationAccountAccessRole`** created in each member account allows administration from the management account;
- the **root user** of the management account is protected by MFA.

### 4. Cost guardrail

- A **monthly cost budget** in the management account covers the whole organization, with email alerts at 85 % and 100 % of actual cost and at 100 % of forecasted cost.
- Credits and refunds are excluded, so the budget reflects real consumption.

## Rationale

- **Isolation (blast radius):** a security incident or a misconfiguration in the workload account does not affect the management account, which holds identity and billing.
- **Temporary credentials** remove the main risk of long-lived access keys being leaked.
- **Centralized identity:** one identity and one MFA give access to every account; adding an account only requires a new assignment.
- These choices follow the IAM security best practices and the Security pillar of the AWS Well-Architected Framework.

## Consequences

**Positive**
- No access keys anywhere in the project, for humans or (later) for the CI/CD pipeline, which will use OIDC.
- The project can be fully cleaned up by destroying the resources of a single account.
- The structure scales: a `prod` account can be added later with the same access model.

**Negative / accepted risks**
- **Single-Region Identity Center:** if eu-west-3 is unavailable, sign-in through the access portal is unavailable. Accepted for a personal project; break-glass access covers this case.
- **Broad permissions:** the only permission set grants full administrator access. Acceptable for a single-person project, but not least privilege.
- **Budgets only alert:** they do not stop spending. Reacting to an alert is a manual action.

## Alternatives not chosen

- **Single account:** simpler, but mixes this project with other workloads and goes against AWS multi-account guidance.
- **IAM users with access keys:** long-lived credentials are the most common cause of AWS account compromise.
- **Multi-Region Identity Center:** requires a customer managed multi-Region KMS key (additional cost and key-policy management) for a resilience benefit not needed here.
- **AWS Control Tower:** provides a full landing zone, but its baseline (AWS Config, centralized logging accounts) adds cost and complexity disproportionate to this project.

## Future improvements

- Add a least-privilege permission set (for example, read-only) for day-to-day inspection.
- Apply Service Control Policies (SCPs), for example to restrict usable Regions.
- Enable centralized root access management to remove root credentials from member accounts.
- Create an organization-wide CloudTrail trail.
