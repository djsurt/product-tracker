output "site_url" {
  value = "https://${var.project}.${replace(aws_eip.web.public_ip, ".", "-")}.sslip.io"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.app.name
}

output "instance_id" {
  value = aws_instance.web.id
}

output "github_role_arn" {
  value = aws_iam_role.github.arn
}

output "region" {
  value = var.region
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}
