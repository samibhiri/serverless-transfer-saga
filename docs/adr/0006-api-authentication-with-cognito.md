# ADR-0006: API authentication with Amazon Cognito

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

The API must only accept requests from known users. Authentication should be handled before any application code runs, without managing passwords or token signing in the project's own code.

## Decision

- Users are managed in an **Amazon Cognito user pool**. They sign in with their **email address**.
- **No self sign-up:** users are created by an administrator (`AdminCreateUser`), with a temporary password changed at first sign-in.
- **Password policy:** at least 12 characters, with lowercase, uppercase, digits and symbols. **TOTP MFA** is available to users (optional).
- One **public app client**, without a client secret. Allowed flows: **SRP** (the password never leaves the client), **refresh token**, and **USER_PASSWORD_AUTH**, enabled to test the API from the command line in `dev`.
- Tokens are valid for **1 hour**; refresh tokens for **1 day**; token revocation is enabled; user existence errors are hidden.
- Every API method uses a **Cognito user pool authorizer**: API Gateway validates the **ID token** sent in the `Authorization` header (signature, issuer, expiry) and rejects invalid requests with `401` before invoking Lambda.

## Rationale

- **Managed identity:** Cognito stores passwords, enforces the policy, handles MFA and signs tokens; the project never sees a password.
- **Authentication at the edge:** unauthenticated requests are rejected by API Gateway, at no Lambda cost and without reaching application code.
- **Admin-only user creation** keeps a public demo API closed to strangers.
- The Cognito user pool authorizer is native to API Gateway REST APIs (ADR-0005) and needs no custom code.

## Consequences

**Positive**
- No credentials or token validation logic in the application.
- The function receives the verified identity of the caller (`requestContext.authorizer.claims`), which allows authorization checks in code.

**Negative / accepted risks**
- **Authentication is not authorization.** A valid token proves who the caller is, not which objects they may access. Access to accounts is restricted to their owner in the application: see [ADR-0007](0007-authorization-by-account-ownership.md).
- **ID token rather than access token with scopes:** simpler, but no fine-grained OAuth scopes per route. Scopes would require a resource server and OAuth flows.
- **USER_PASSWORD_AUTH** sends the password to Cognito (over TLS). Accepted in `dev` for command-line tests; to be removed when a front end uses SRP.
- **MFA is optional**, not required. Acceptable for fictitious data; a production system handling money would require it.
- Users are created manually, outside Terraform, so that no password is stored in the Terraform state.

## Alternatives not chosen

- **IAM authorization (SigV4):** strong for service-to-service calls, but unsuited to end users.
- **Lambda authorizer:** flexible, but custom code to write, test and run on every request.
- **API keys alone:** they identify a client application, not a user; AWS does not recommend them for authentication.
- **External identity provider** (Auth0, Okta, Entra ID): possible through Cognito federation; not needed for this project.
