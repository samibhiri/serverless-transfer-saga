# Authentication of API users with an Amazon Cognito user pool (ADR-0006).
# Users are created by an administrator only: there is no public sign-up.

resource "aws_cognito_user_pool" "this" {
  name = "${local.name_prefix}-users"

  # Users sign in with their email address.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  # No self sign-up: accounts are created by an administrator.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  # Users may enable an authenticator app (TOTP) as a second factor.
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Disabled in dev so that the environment can be destroyed between phases.
  deletion_protection = var.deletion_protection ? "ACTIVE" : "INACTIVE"
}

# Public client used to sign in from scripts and, later, a front end.
# No client secret: a secret cannot be kept in a public client.
resource "aws_cognito_user_pool_client" "api" {
  name         = "${local.name_prefix}-api-client"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false

  # SRP: the password never leaves the client.
  # USER_PASSWORD_AUTH: the password is sent to Cognito over TLS; enabled only
  # to test the API from the command line in dev.
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Same response whether the user exists or not (no user enumeration).
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 1

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}
