output "accounts_table_name" {
  description = "Name of the DynamoDB table that stores the accounts."
  value       = aws_dynamodb_table.accounts.name
}
