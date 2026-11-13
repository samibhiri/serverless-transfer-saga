# Transfer Saga (ADR-0008): the two Lambda functions it calls, and the
# Step Functions state machine that orchestrates them.

locals {
  saga_functions = {
    saga-steps = {
      source      = "saga_steps"
      description = "Transfer Saga steps: debit, credit, refund (idempotent)."
      environment = {
        ACCOUNTS_TABLE_NAME  = aws_dynamodb_table.accounts.name
        TRANSFERS_TABLE_NAME = aws_dynamodb_table.transfers.name
        LOG_LEVEL            = "INFO"
      }
    }
    fraud-check = {
      source      = "fraud_check"
      description = "Simulated external fraud check service, intentionally unreliable."
      environment = {
        FAILURE_RATE        = tostring(var.fraud_check_failure_rate)
        REJECTION_THRESHOLD = tostring(var.fraud_check_rejection_threshold)
        LOG_LEVEL           = "INFO"
      }
    }
  }
}

# --- Packages and logs -----------------------------------------------------------------

data "archive_file" "saga" {
  for_each = local.saga_functions

  type        = "zip"
  source_file = "${path.module}/../../../services/${each.value.source}/app.py"
  output_path = "${path.module}/build/${each.key}.zip"
}

resource "aws_cloudwatch_log_group" "saga" {
  for_each = local.saga_functions

  name              = "/aws/lambda/${local.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

# --- Execution roles (one per function, least privilege) ---------------------------------

resource "aws_iam_role" "saga" {
  for_each = local.saga_functions

  name               = "${local.name_prefix}-${each.key}-function"
  description        = "Execution role of the ${each.key} Lambda function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "saga_steps" {
  statement {
    sid       = "ReadTransfers"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.transfers.arn]
  }

  # TransactWriteItems is authorized on each of its operations: here UpdateItem.
  statement {
    sid       = "MoveMoneyAndMarkSteps"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.accounts.arn, aws_dynamodb_table.transfers.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.saga["saga-steps"].arn}:*"]
  }
}

data "aws_iam_policy_document" "fraud_check" {
  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.saga["fraud-check"].arn}:*"]
  }
}

resource "aws_iam_role_policy" "saga" {
  for_each = local.saga_functions

  name = "${each.key}-function"
  role = aws_iam_role.saga[each.key].id
  policy = {
    saga-steps  = data.aws_iam_policy_document.saga_steps.json
    fraud-check = data.aws_iam_policy_document.fraud_check.json
  }[each.key]
}

# --- Functions ---------------------------------------------------------------------------

resource "aws_lambda_function" "saga" {
  for_each = local.saga_functions

  function_name = "${local.name_prefix}-${each.key}"
  description   = each.value.description
  role          = aws_iam_role.saga[each.key].arn

  runtime       = "python3.13"
  handler       = "app.handler"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 10

  filename         = data.archive_file.saga[each.key].output_path
  source_code_hash = data.archive_file.saga[each.key].output_base64sha256

  environment {
    variables = each.value.environment
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.saga[each.key].name
  }

  depends_on = [aws_iam_role_policy.saga]
}

# --- State machine ----------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "transfer_saga" {
  # The /aws/vendedlogs/ prefix is recommended for Step Functions logs: it avoids
  # the size limit of CloudWatch Logs resource policies.
  name              = "/aws/vendedlogs/states/${local.name_prefix}-transfer-saga"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "states_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    # Confused deputy protection: only state machines of this account.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "transfer_saga" {
  name               = "${local.name_prefix}-transfer-saga"
  description        = "Execution role of the transfer Saga state machine."
  assume_role_policy = data.aws_iam_policy_document.states_assume_role.json
}

data "aws_iam_policy_document" "transfer_saga" {
  statement {
    sid       = "InvokeSagaFunctions"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [for function in aws_lambda_function.saga : function.arn]
  }

  # Direct DynamoDB integration: read the transfer, record its final status.
  statement {
    sid       = "ReadAndFinalizeTransfers"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.transfers.arn]
  }

  # Log delivery for Step Functions: AWS requires these actions on all resources.
  statement {
    sid    = "DeliverLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "transfer_saga" {
  name   = "transfer-saga"
  role   = aws_iam_role.transfer_saga.id
  policy = data.aws_iam_policy_document.transfer_saga.json
}

resource "aws_sfn_state_machine" "transfer_saga" {
  name     = "${local.name_prefix}-transfer-saga"
  type     = "STANDARD"
  role_arn = aws_iam_role.transfer_saga.arn

  definition = templatefile("${path.module}/statemachines/transfer-saga.asl.json", {
    transfers_table      = aws_dynamodb_table.transfers.name
    fraud_check_function = aws_lambda_function.saga["fraud-check"].arn
    saga_steps_function  = aws_lambda_function.saga["saga-steps"].arn
  })

  # Errors only, without execution data: balances and identities stay out of the logs.
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.transfer_saga.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.transfer_saga]
}
