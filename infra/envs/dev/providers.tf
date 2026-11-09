provider "aws" {
  region = var.region

  # Safety net: refuse to run against any account other than the workload account.
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project     = "serverless-transfer-saga"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
