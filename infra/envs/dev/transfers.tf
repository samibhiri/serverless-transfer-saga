# Transfers service: the Lambda function behind POST /transfers and
# GET /transfers/{transfer_id} (ADR-0004, ADR-0008).

locals {
  transfers_function_name = "${local.name_prefix}-transfers"
}

data "archive_file" "transfers" {
  type        = "zip"
  source_file = "${path.module}/../../../services/transfers/app.py"
  output_path = "${path.module}/build/transfers.zip"
}

resource "aws_cloudwatch_log_group" "transfers" {
  name              = "/aws/lambda/${local.transfers_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "transfers" {
  name               = "${local.transfers_function_name}-function"
  description        = "Execution role of the transfers Lambda function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Least privilege: check accounts, record transfers, start the Saga. No money moves here.
data "aws_iam_policy_document" "transfers" {
  statement {
    sid       = "ReadAccounts"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.accounts.arn]
  }

  statement {
    sid       = "ReadAndCreateTransfers"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.transfers.arn]
  }

  statement {
    sid       = "StartTransferSaga"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.transfer_saga.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.transfers.arn}:*"]
  }
}

resource "aws_iam_role_policy" "transfers" {
  name   = "transfers-function"
  role   = aws_iam_role.transfers.id
  policy = data.aws_iam_policy_document.transfers.json
}

resource "aws_lambda_function" "transfers" {
  function_name = local.transfers_function_name
  description   = "Transfers API: request a transfer and read its status."
  role          = aws_iam_role.transfers.arn

  runtime       = "python3.13"
  handler       = "app.handler"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 10

  filename         = data.archive_file.transfers.output_path
  source_code_hash = data.archive_file.transfers.output_base64sha256

  environment {
    variables = {
      ACCOUNTS_TABLE_NAME  = aws_dynamodb_table.accounts.name
      TRANSFERS_TABLE_NAME = aws_dynamodb_table.transfers.name
      STATE_MACHINE_ARN    = aws_sfn_state_machine.transfer_saga.arn
      LOG_LEVEL            = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.transfers.name
  }

  depends_on = [aws_iam_role_policy.transfers]
}
