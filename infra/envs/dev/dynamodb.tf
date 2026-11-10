# Accounts table: one item per (fictitious) bank account.
#   account_id (partition key) | owner | balance | currency | created_at
# Balances are stored in minor units (cents) as integers, never as floats.

resource "aws_dynamodb_table" "accounts" {
  name         = "${local.name_prefix}-accounts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "account_id"

  attribute {
    name = "account_id"
    type = "S"
  }

  # Continuous backups: restore the table to any second of the last 35 days.
  point_in_time_recovery {
    enabled = true
  }

  # Encryption at rest is always on in DynamoDB, with an AWS owned key by default
  # (no server_side_encryption block needed). A customer managed KMS key is
  # evaluated in the security phase.

  # Disabled in dev so that the environment can be destroyed between phases.
  deletion_protection_enabled = var.deletion_protection
}
