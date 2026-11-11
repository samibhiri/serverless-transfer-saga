# ADR-0007: Authorization by account ownership

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

Since ADR-0006, every request to the API carries a token verified by API Gateway: the caller is **authenticated**. But the API did not check whether the caller may access the **object** they ask for. Any signed-in user could read any account by its identifier.

This is **Broken Object Level Authorization (BOLA)**, the first risk of the OWASP API Security Top 10. In addition, the owner of an account was a free-text name sent by the client, not linked to the signed-in user.

## Decision

- **The owner of an account is the authenticated caller**, never a value sent by the client. At creation, the function stores `owner_id` = the `sub` claim of the caller's token, read from `requestContext.authorizer.claims` (set by API Gateway after verifying the token). The `owner` field of the request is only a display name.
- **A user can only read their own accounts.** If the account does not exist **or** belongs to another user, the API returns the **same** `404 Not Found` response.
- **Defense in depth:** if the verified identity is missing from the request, the function returns `401`, even though the API Gateway authorizer should already have rejected it.
- Unit tests cover these rules, including "a user cannot read another user's account".

## Rationale

- `sub` is the stable, unique identifier of a Cognito user; unlike the email, it never changes.
- Claims come from a token verified by API Gateway; values in the request body are under the client's control.
- Returning `404` instead of `403` avoids confirming that an identifier exists, which would help an attacker enumerate accounts.
- Checking identity in the function does not rely on the API configuration being always correct.

## Consequences

**Positive**
- Each user's accounts and balances are isolated from other users.
- The rule is enforced and tested in a single place.

**Negative / accepted**
- **Authorization lives in application code**, not in IAM: the function's IAM role can still read every item of the table. A bug in this check would expose other accounts; the unit tests guard against regressions.
- Listing "my accounts" will need a secondary index on `owner_id` (not needed yet).
- There is no administrator role able to read every account; not needed for this project.

## Alternatives not chosen

- **DynamoDB fine-grained access control** (IAM condition `dynamodb:LeadingKeys` on the user's identity): enforces isolation in IAM itself, but requires per-user AWS credentials (Cognito identity pools) and a table keyed by owner. Too complex for the current design; a possible hardening for the security phase.
- **`403 Forbidden` for foreign accounts:** clearer for debugging, but reveals that the account exists.
- **Owner given in the request body:** trivially spoofable.
