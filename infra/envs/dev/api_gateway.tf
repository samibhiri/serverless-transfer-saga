# Public REST API of the project (ADR-0005).
#
#   POST /accounts                 -> accounts Lambda function
#   GET  /accounts/{account_id}    -> accounts Lambda function
#
# Every method requires a valid Cognito ID token (ADR-0006).

resource "aws_api_gateway_rest_api" "this" {
  name        = "${local.name_prefix}-api"
  description = "Serverless Transfer Saga API."

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# --- Resources (paths) -----------------------------------------------------------

resource "aws_api_gateway_resource" "accounts" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "accounts"
}

resource "aws_api_gateway_resource" "account" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.accounts.id
  path_part   = "{account_id}"
}

# --- Authorizer -------------------------------------------------------------------------
# API Gateway validates the token (signature, issuer, expiry) before calling Lambda:
# unauthenticated requests are rejected with 401 and never reach the function.

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito"
  rest_api_id     = aws_api_gateway_rest_api.this.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.this.arn]
  identity_source = "method.request.header.Authorization"
}

# --- Methods and Lambda proxy integrations ------------------------------------------

resource "aws_api_gateway_method" "create_account" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.accounts.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "create_account" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.accounts.id
  http_method             = aws_api_gateway_method.create_account.http_method
  type                    = "AWS_PROXY" # the whole request is passed to Lambda as an event
  integration_http_method = "POST"      # Lambda is always invoked with POST
  uri                     = aws_lambda_function.accounts.invoke_arn
}

resource "aws_api_gateway_method" "get_account" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.account.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.account_id" = true
  }
}

resource "aws_api_gateway_integration" "get_account" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.account.id
  http_method             = aws_api_gateway_method.get_account.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.accounts.invoke_arn
}

# API Gateway may invoke the function, only for these two routes of this API.
resource "aws_lambda_permission" "api_create_account" {
  statement_id  = "AllowApiGatewayCreateAccount"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.accounts.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/POST/accounts"
}

resource "aws_lambda_permission" "api_get_account" {
  statement_id  = "AllowApiGatewayGetAccount"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.accounts.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/GET/accounts/*"
}

# --- Deployment and stage -------------------------------------------------------------

# A deployment is a snapshot of the API. A new one is created whenever a path,
# method or integration changes.
resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_authorizer.cognito,
      aws_api_gateway_resource.accounts,
      aws_api_gateway_resource.account,
      aws_api_gateway_method.create_account,
      aws_api_gateway_integration.create_account,
      aws_api_gateway_method.get_account,
      aws_api_gateway_integration.get_account,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name_prefix}-api/access"
  retention_in_days = var.log_retention_days
}

resource "aws_api_gateway_stage" "this" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.environment

  # One JSON line per request: who called what, the result and the latency.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      sourceIp           = "$context.identity.sourceIp"
      httpMethod         = "$context.httpMethod"
      resourcePath       = "$context.resourcePath"
      status             = "$context.status"
      responseLength     = "$context.responseLength"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      errorMessage       = "$context.error.message"
    })
  }

  depends_on = [aws_api_gateway_account.this]
}

# Throttling (rate limiting) and metrics for every method of the stage.
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.this.stage_name
  method_path = "*/*"

  settings {
    metrics_enabled        = true
    logging_level          = "ERROR"
    throttling_rate_limit  = var.api_throttling_rate_limit
    throttling_burst_limit = var.api_throttling_burst_limit
  }
}

# --- Account-level setting: allow API Gateway to write to CloudWatch Logs --------------
# This setting is per account and per Region; it is managed here because the
# workload account hosts a single environment.

data "aws_iam_policy_document" "api_gateway_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_gateway_cloudwatch" {
  name               = "${local.name_prefix}-apigateway-cloudwatch"
  description        = "Allows API Gateway to write logs to CloudWatch Logs."
  assume_role_policy = data.aws_iam_policy_document.api_gateway_assume_role.json
}

resource "aws_iam_role_policy_attachment" "api_gateway_cloudwatch" {
  role       = aws_iam_role.api_gateway_cloudwatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  cloudwatch_role_arn = aws_iam_role.api_gateway_cloudwatch.arn

  depends_on = [aws_iam_role_policy_attachment.api_gateway_cloudwatch]
}
