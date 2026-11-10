# Accounts service: one Lambda function for every route of the service (ADR-0004).

locals {
  accounts_function_name = "${local.name_prefix}-accounts"
}

# --- Package -------------------------------------------------------------------
# The function has no dependency (boto3 is provided by the runtime):
# the package contains only app.py. Built at plan time, never committed.

data "archive_file" "accounts" {
  type        = "zip"
  source_file = "${path.module}/../../../services/accounts/app.py"
  output_path = "${path.module}/build/accounts.zip"
}

# --- Logs ----------------------------------------------------------------------
# Created by Terraform (not by Lambda on first run) to control the retention.

resource "aws_cloudwatch_log_group" "accounts" {
  name              = "/aws/lambda/${local.accounts_function_name}"
  retention_in_days = var.log_retention_days
}

# --- Execution role --------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "accounts" {
  name               = "${local.accounts_function_name}-function"
  description        = "Execution role of the accounts Lambda function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Least privilege: only the actions the service uses, only on its own resources.
data "aws_iam_policy_document" "accounts" {
  statement {
    sid       = "AccountsTableReadWrite"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.accounts.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.accounts.arn}:*"]
  }
}

resource "aws_iam_role_policy" "accounts" {
  name   = "accounts-function"
  role   = aws_iam_role.accounts.id
  policy = data.aws_iam_policy_document.accounts.json
}

# --- Function ----------------------------------------------------------------------

resource "aws_lambda_function" "accounts" {
  function_name = local.accounts_function_name
  description   = "Accounts API: create and read accounts."
  role          = aws_iam_role.accounts.arn

  runtime       = "python3.13"
  handler       = "app.handler"
  architectures = ["arm64"] # Graviton: about 20 % cheaper than x86 for the same duration
  memory_size   = 256
  timeout       = 10

  filename         = data.archive_file.accounts.output_path
  source_code_hash = data.archive_file.accounts.output_base64sha256

  environment {
    variables = {
      ACCOUNTS_TABLE_NAME = aws_dynamodb_table.accounts.name
      LOG_LEVEL           = "INFO"
    }
  }

  # Structured JSON logs, written to the log group managed above.
  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.accounts.name
  }

  depends_on = [aws_iam_role_policy.accounts]
}
