#!/usr/bin/env bash
# End-to-end test of the accounts API, against the deployed dev environment.
#
# Prerequisites:
#   - the dev environment is deployed (workflow "Terraform deploy");
#   - an AWS session on the workload account: aws sso login --profile <profile>
#     and AWS_PROFILE set;
#   - Terraform initialized in infra/envs/dev (terraform init -backend-config=backend.hcl).
#
# Usage:
#   scripts/e2e-accounts.sh
#
# The script creates two test users (alice@example.com and bob@example.com) if
# they do not exist, asks for their password (never stored), then checks
# authentication, authorization and validation rules. It exits with a non-zero
# status if any check fails.

set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/../infra/envs/dev" && pwd)"
API_URL=$(terraform -chdir="$TF_DIR" output -raw api_url)
USER_POOL_ID=$(terraform -chdir="$TF_DIR" output -raw user_pool_id)
CLIENT_ID=$(terraform -chdir="$TF_DIR" output -raw user_pool_client_id)

if [[ -z "${TEST_PASSWORD:-}" ]]; then
  read -r -s -p "Password of the test users (min. 12 chars, upper, lower, digit, symbol): " TEST_PASSWORD
  echo
fi

FAILURES=0

# --- Test users -----------------------------------------------------------------

ensure_user() {
  local email="$1"
  if aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$email" >/dev/null 2>&1; then
    echo "User exists: $email"
  else
    aws cognito-idp admin-create-user \
      --user-pool-id "$USER_POOL_ID" \
      --username "$email" \
      --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
      --message-action SUPPRESS >/dev/null
    echo "User created: $email"
  fi
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" --username "$email" \
    --password "$TEST_PASSWORD" --permanent
}

get_token() {
  aws cognito-idp initiate-auth \
    --client-id "$CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters USERNAME="$1",PASSWORD="$TEST_PASSWORD" \
    --query "AuthenticationResult.IdToken" --output text
}

# --- HTTP helpers -----------------------------------------------------------------

BODY=""
STATUS=""
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT

# call <method> <path> <token or empty> [json body]
call() {
  local method="$1" path="$2" token="$3" data="${4:-}"
  local args=(-s -o "$BODY_FILE" -w "%{http_code}" -X "$method" "$API_URL$path")
  [[ -n "$token" ]] && args+=(-H "Authorization: $token")
  [[ -n "$data" ]] && args+=(-H "Content-Type: application/json" -d "$data")
  STATUS=$(curl "${args[@]}")
  BODY=$(cat "$BODY_FILE")
}

# check <description> <expected status>
check() {
  if [[ "$STATUS" == "$2" ]]; then
    echo "  PASS  $1 (HTTP $STATUS)"
  else
    echo "  FAIL  $1 (expected HTTP $2, got $STATUS): $BODY"
    FAILURES=$((FAILURES + 1))
  fi
}

# --- Tests ------------------------------------------------------------------------

echo "== Test users"
ensure_user alice@example.com
ensure_user bob@example.com
ALICE_TOKEN=$(get_token alice@example.com)
BOB_TOKEN=$(get_token bob@example.com)

UNKNOWN_ID="00000000-0000-0000-0000-000000000000"

echo "== Authentication (API Gateway, before Lambda)"
call GET "/accounts/$UNKNOWN_ID" ""
check "no token is rejected" 401
call GET "/accounts/$UNKNOWN_ID" "${ALICE_TOKEN}x"
check "tampered token is rejected" 403

echo "== Create and read an account"
call POST /accounts "$ALICE_TOKEN" '{"owner": "Alice", "initial_balance": 50000}'
check "Alice creates an account" 201
ALICE_ACCOUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('account_id', ''))" "$BODY")
call GET "/accounts/$ALICE_ACCOUNT" "$ALICE_TOKEN"
check "Alice reads her account" 200

echo "== Authorization (ADR-0007)"
call GET "/accounts/$ALICE_ACCOUNT" "$BOB_TOKEN"
check "Bob cannot read Alice's account" 404
call POST /accounts "$BOB_TOKEN" '{"owner": "Bob", "owner_id": "spoofed"}'
check "Bob creates an account" 201
OWNER_ID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('owner_id', ''))" "$BODY")
if [[ "$OWNER_ID" == "spoofed" || -z "$OWNER_ID" ]]; then
  echo "  FAIL  owner_id must come from the token, got: $OWNER_ID"
  FAILURES=$((FAILURES + 1))
else
  echo "  PASS  owner_id comes from the token, not from the body"
fi

echo "== Validation"
call POST /accounts "$ALICE_TOKEN" '{"owner": "Alice", "initial_balance": 10.5}'
check "decimal amount is rejected" 400
call GET "/accounts/not-a-uuid" "$ALICE_TOKEN"
check "invalid account id is rejected" 400

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "All checks passed."
else
  echo "$FAILURES check(s) failed."
  exit 1
fi
