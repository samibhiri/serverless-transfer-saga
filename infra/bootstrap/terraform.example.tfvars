# Copy this file to terraform.tfvars and set your own values.
# terraform.tfvars is ignored by Git: account IDs are not published in this repository.

account_id        = "123456789012"
github_repository = "your-github-user/serverless-transfer-saga"

# Numeric IDs of the owner and of the repository, used in the OIDC subject claim.
# To find them: curl -s https://api.github.com/repos/<owner>/<repo>
#   owner ID      = .owner.id
#   repository ID = .id
github_owner_id      = "1234567"
github_repository_id = "123456789"
