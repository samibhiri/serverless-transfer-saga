terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # The bootstrap state lives in the bucket that this configuration creates.
  # The bucket name contains the account ID: it is passed at init time with
  # -backend-config=backend.hcl (ignored by Git), see backend.example.hcl.
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-west-3"
    encrypt      = true
    use_lockfile = true
  }
}
