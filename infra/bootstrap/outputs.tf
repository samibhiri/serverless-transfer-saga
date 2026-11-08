output "state_bucket_name" {
  description = "Name of the S3 bucket that stores Terraform state."
  value       = aws_s3_bucket.state.id
}

output "state_bucket_region" {
  description = "Region of the Terraform state bucket."
  value       = var.region
}

output "plan_role_arn" {
  description = "ARN of the IAM role assumed by GitHub Actions to run terraform plan."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "ARN of the IAM role assumed by GitHub Actions to run terraform apply."
  value       = aws_iam_role.deploy.arn
}
