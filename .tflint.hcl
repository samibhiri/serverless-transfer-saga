# TFLint configuration, shared by every Terraform configuration under infra/.
# The Terraform ruleset is bundled with TFLint: no plugin download is needed.

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}
