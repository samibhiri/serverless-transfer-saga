variable "account_id" {
  description = "ID of the AWS workload account. Terraform refuses to run against any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "region" {
  description = "AWS Region where the Terraform state bucket is created."
  type        = string
  default     = "eu-west-3"
}

variable "state_bucket_prefix" {
  description = "Prefix of the state bucket name. The account ID and Region are appended to make it globally unique."
  type        = string
  default     = "transfer-saga-tfstate"
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the pipeline roles, in the form owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be in the form owner/name."
  }
}

variable "github_deploy_environment" {
  description = "Name of the GitHub environment whose jobs may assume the deploy role."
  type        = string
  default     = "dev"
}

variable "noncurrent_version_retention_days" {
  description = "Number of days previous versions of state files are kept before deletion."
  type        = number
  default     = 90
}
