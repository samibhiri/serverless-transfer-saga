terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # State stored in the bucket created by infra/bootstrap, under its own key.
  # The bucket name is passed at init time: -backend-config=backend.hcl locally,
  # -backend-config="bucket=..." in the pipeline.
  backend "s3" {
    key          = "envs/dev/terraform.tfstate"
    region       = "eu-west-3"
    encrypt      = true
    use_lockfile = true
  }
}
