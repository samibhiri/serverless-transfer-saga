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

variable "api_throttling_rate_limit" {
  description = "Steady-state request rate allowed on each API method, in requests per second."
  type        = number
  default     = 10
}

variable "api_throttling_burst_limit" {
  description = "Maximum burst of concurrent requests allowed on each API method."
  type        = number
  default     = 20
}

variable "fraud_check_failure_rate" {
  description = "Probability (0 to 1) that the simulated fraud check fails with a temporary error."
  type        = number
  default     = 0.2

  validation {
    condition     = var.fraud_check_failure_rate >= 0 && var.fraud_check_failure_rate <= 1
    error_message = "fraud_check_failure_rate must be between 0 and 1."
  }
}

variable "fraud_check_rejection_threshold" {
  description = "Transfers above this amount (in minor units) are rejected by the simulated fraud check."
  type        = number
  default     = 500000
}
