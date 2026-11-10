variable "account_id" {
  description = "ID of the AWS workload account. Terraform refuses to run against any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "region" {
  description = "AWS Region where the application is deployed."
  type        = string
  default     = "eu-west-3"
}

variable "environment" {
  description = "Name of the environment, used in tags and resource names."
  type        = string
  default     = "dev"
}

variable "deletion_protection" {
  description = "Protect stateful resources (DynamoDB tables) against deletion. Disabled in dev so that the environment can be destroyed between phases."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Retention of the Lambda log groups, in days."
  type        = number
  default     = 14
}
