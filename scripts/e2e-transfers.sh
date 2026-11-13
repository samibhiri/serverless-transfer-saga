#!/usr/bin/env bash
# End-to-end test of the transfer Saga, against the deployed dev environment.
#
# Prerequisites:
#   - the dev environment is deployed (workflow "Terraform deploy");
#   - an AWS session on the workload account: aws sso login --profile <profile>
#     and AWS_PROFILE set;
#   - Terraform initialized in infra/envs/dev (terraform init -backend-config=backend.hcl).
#
# Usage:
#   scripts/e2e-transfers.sh
#
# The script creates two test users (alice@example.com and bob@example.com) if
# they do not exist, asks for their password (never stored), creates fresh
# accounts, and checks the four outcomes of a transfer (COMPLETED, REJECTED,
# FAILED, COMPENSATED), idempotency and authorization. It exits with a non-zero
# status if any check fails.
#
# To trigger a compensation, the script closes a target account directly in
# DynamoDB (status = CLOSED), as an administrator would.

set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/../infra/envs/dev" && pwd)"
API_URL=$(terraform -chdir="$TF_DIR" output -raw api_url)
USER_POOL_ID=$(terraform -chdir="$TF_DIR" output -raw user_pool_id)
CLIENT_ID=$(terraform -chdir="$TF_DIR" output -raw user_pool_client_id)
ACCOUNTS_TABLE=$(terraform -chdir="$TF_DIR" output -raw accounts_table_name)

if [[ -z "${TEST_PASSWORD:-}" ]]; then
  read -r -s -p "Password of the test users (min. 12 chars, upper, lower, digit, symbol): " TEST_PASSWORD
  echo
fi

FAILURES=0
BODY=""
STATUS=""
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT

# --- Test users -----------------------------------------------------------------

ensure_user() {
  local email="$1"
  if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$email" >/dev/null 2>&1; then
    aws cognito-idp admin-create-user \
      --user-pool-id "$USER_POOL_ID" \
      --username "$email" \
      --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
      --message-action SUPPRESS >/dev/null
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

# --- HTTP and JSON helpers -----------------------------------------------------------

# call <method> <path> <token> [json body] [idempotency key]
call() {
  local method="$1" path="$2" token="$3" data="${4:-}" key="${5:-}"
  local args=(-s -o "$BODY_FILE" -w "%{http_code}" -X "$method" "$API_URL$path" -H "Authorization: $token")
  [[ -n "$data" ]] && args+=(-H "Content-Type: application/json" -d "$data")
  [[ -n "$key" ]] && args+=(-H "Idempotency-Key: $key")
  STATUS=$(curl "${args[@]}")
  BODY=$(cat "$BODY_FILE")
}

# json <field>: read a field of the last response body
json() {
  python3 -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], ''))" "$BODY" "$1"
}

new_key() {
  python3 -c "import uuid; print(uuid.uuid4())"
}

# new_account <token> <owner> <initial balance> -> prints the account id
new_account() {
  call POST /accounts "$1" "{\"owner\": \"$2\", \"initial_balance\": $3}"
  [[ "$STATUS" == "201" ]] || { echo "Cannot create an account: HTTP $STATUS $BODY" >&2; exit 1; }
  json account_id
}

balance() {
  call GET "/accounts/$2" "$1"
  json balance
}

# transfer <token> <source> <target> <amount> <key>
transfer() {
  call POST /transfers "$1" \
    "{\"source_account_id\": \"$2\", \"target_account_id\": \"$3\", \"amount\": $4}" "$5"
}

# wait_final <token> <transfer id>: poll until the transfer leaves PENDING (60 s max).
# Sets FINAL to the final status; BODY then holds the final transfer.
# Not called through $(...): a subshell would lose BODY.
FINAL=""
wait_final() {
  for _ in $(seq 1 30); do
    call GET "/transfers/$2" "$1"
    FINAL=$(json status)
    [[ "$FINAL" != "PENDING" ]] && return
    sleep 2
  done
}

# --- Assertions ----------------------------------------------------------------------

check() {  # check <description> <expected> <actual>
  if [[ "$3" == "$2" ]]; then
    echo "  PASS  $1 ($3)"
  else
    echo "  FAIL  $1 (expected $2, got $3)"
    FAILURES=$((FAILURES + 1))
  fi
}

# --- Tests ---------------------------------------------------------------------------

echo "== Test users and accounts"
ensure_user alice@example.com
ensure_user bob@example.com
ALICE=$(get_token alice@example.com)
BOB=$(get_token bob@example.com)

echo "== 1. Successful transfer: COMPLETED"
A1=$(new_account "$ALICE" Alice 50000)
B1=$(new_account "$BOB" Bob 0)
KEY=$(new_key)
transfer "$ALICE" "$A1" "$B1" 3000 "$KEY"
check "request accepted" 202 "$STATUS"
T1=$(json transfer_id)
wait_final "$ALICE" "$T1"
check "final status" COMPLETED "$FINAL"
check "source balance" 47000 "$(balance "$ALICE" "$A1")"
check "target balance" 3000 "$(balance "$BOB" "$B1")"

echo "== 2. Idempotency"
transfer "$ALICE" "$A1" "$B1" 3000 "$KEY"
check "same key, same request: accepted" 202 "$STATUS"
check "same key, same request: same transfer" "$T1" "$(json transfer_id)"
sleep 3
check "same key, same request: no second debit" 47000 "$(balance "$ALICE" "$A1")"
transfer "$ALICE" "$A1" "$B1" 9999 "$KEY"
check "same key, different request: refused" 422 "$STATUS"
transfer "$ALICE" "$A1" "$B1" 3000 ""
check "missing Idempotency-Key: refused" 400 "$STATUS"

echo "== 3. Fraud check refuses the transfer: REJECTED"
A3=$(new_account "$ALICE" Alice 1000000)
transfer "$ALICE" "$A3" "$B1" 600000 "$(new_key)"
check "request accepted" 202 "$STATUS"
wait_final "$ALICE" "$(json transfer_id)"
check "final status" REJECTED "$FINAL"
check "failure reason" "Amount above the fraud threshold" "$(json failure_reason)"
check "source balance unchanged" 1000000 "$(balance "$ALICE" "$A3")"

echo "== 4. Insufficient funds: FAILED"
A4=$(new_account "$ALICE" Alice 5000)
transfer "$ALICE" "$A4" "$B1" 9000 "$(new_key)"
check "request accepted" 202 "$STATUS"
wait_final "$ALICE" "$(json transfer_id)"
check "final status" FAILED "$FINAL"
check "failure reason" InsufficientFunds "$(json failure_reason)"
check "source balance unchanged" 5000 "$(balance "$ALICE" "$A4")"

echo "== 5. Target account closed: COMPENSATED"
A5=$(new_account "$ALICE" Alice 20000)
B5=$(new_account "$BOB" Bob 100)
aws dynamodb update-item --table-name "$ACCOUNTS_TABLE" \
  --key "{\"account_id\": {\"S\": \"$B5\"}}" \
  --update-expression "SET #s = :closed" \
  --expression-attribute-names '{"#s": "status"}' \
  --expression-attribute-values '{":closed": {"S": "CLOSED"}}'
transfer "$ALICE" "$A5" "$B5" 7000 "$(new_key)"
check "request accepted" 202 "$STATUS"
T5=$(json transfer_id)
wait_final "$ALICE" "$T5"
check "final status" COMPENSATED "$FINAL"
check "failure reason" CreditRejected "$(json failure_reason)"
check "debit and refund recorded" "yes" "$(python3 -c "import json,sys; t=json.loads(sys.argv[1]); print('yes' if t.get('debited_at') and t.get('refunded_at') and not t.get('credited_at') else 'no')" "$BODY")"
check "source balance restored" 20000 "$(balance "$ALICE" "$A5")"
check "target balance unchanged" 100 "$(balance "$BOB" "$B5")"

echo "== 6. Authorization"
call GET "/transfers/$T1" "$BOB"
check "Bob cannot read Alice's transfer" 404 "$STATUS"
B6=$(new_account "$BOB" Bob 10000)
transfer "$ALICE" "$B6" "$A1" 100 "$(new_key)"
check "Alice cannot transfer from Bob's account" 404 "$STATUS"
check "Bob's balance unchanged" 10000 "$(balance "$BOB" "$B6")"

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "All checks passed."
else
  echo "$FAILURES check(s) failed."
  exit 1
fi
