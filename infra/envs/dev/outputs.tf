output "accounts_table_name" {
  description = "Name of the DynamoDB table that stores the accounts."
  value       = aws_dynamodb_table.accounts.name
}

output "accounts_function_name" {
  description = "Name of the accounts Lambda function."
  value       = aws_lambda_function.accounts.function_name
}

output "api_url" {
  description = "Base URL of the API stage."
  value       = aws_api_gateway_stage.this.invoke_url
}

output "user_pool_id" {
  description = "ID of the Cognito user pool."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_client_id" {
  description = "ID of the Cognito app client used to sign in (public client, no secret)."
  value       = aws_cognito_user_pool_client.api.id
}

output "transfers_table_name" {
  description = "Name of the DynamoDB table that stores the transfers."
  value       = aws_dynamodb_table.transfers.name
}

output "transfer_saga_state_machine_arn" {
  description = "ARN of the transfer Saga state machine."
  value       = aws_sfn_state_machine.transfer_saga.arn
}

output "transfers_function_name" {
  description = "Name of the transfers Lambda function."
  value       = aws_lambda_function.transfers.function_name
}
