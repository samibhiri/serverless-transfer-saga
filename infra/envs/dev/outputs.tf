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
