output "review_summary" {
  description = "Non-secret inputs that must be reviewed and sealed before provisioning approval."
  value = {
    region               = var.region
    availability_zone    = local.selected_availability_zone
    instance_type        = var.instance_type
    architecture         = var.architecture
    ami_id               = local.selected_ami_id
    ebs_size_gib         = var.ebs_size_gib
    archive_bucket       = local.archive_bucket_name
    canonical_archive    = local.canonical_archive_prefix
    temporary_archive    = local.temporary_archive_prefix
    cloudwatch_retention = var.cloudwatch_log_retention_days
    lifecycle_enabled    = var.enable_archive_lifecycle
    temporary_expiration = var.enable_temporary_expiration
    disk_thresholds = {
      warning_percent  = var.disk_warning_percent
      high_percent     = var.disk_high_percent
      critical_percent = var.disk_critical_percent
    }
    termination_protection  = var.enable_termination_protection
    security_group_ingress  = "none"
    operations_access       = "SSM Session Manager only"
    dashboard_public_access = "none"
    live_trading            = "disabled"
    alpha_research          = "blocked"
  }
}

output "instance_id" {
  description = "Collector instance ID after an explicitly approved apply."
  value       = aws_instance.collector.id
}

output "archive_bucket_name" {
  description = "Private selective canonical/temporary archive bucket."
  value       = aws_s3_bucket.archive.id
}

output "cloudwatch_log_group" {
  description = "Operational-only CloudWatch log group."
  value       = aws_cloudwatch_log_group.collector.name
}

output "iam_role_arn" {
  description = "Least-privilege instance role; no static AWS access keys are used."
  value       = aws_iam_role.collector.arn
}
