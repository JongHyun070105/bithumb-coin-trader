variable "region" {
  description = "AWS region for the isolated collector epoch."
  type        = string
  default     = "ap-northeast-2"
}

variable "availability_zone" {
  description = "Explicit AZ to seal in epoch provenance. Null selects the first available AZ at plan time."
  type        = string
  default     = null
  nullable    = true
}

variable "project_name" {
  description = "Lowercase name used in tags and generated resource names."
  type        = string
  default     = "bitcoin-trader"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "project_name must contain only lowercase letters, digits, and hyphens."
  }
}

variable "environment_id" {
  description = "Immutable environment identifier for the AWS epoch."
  type        = string
  default     = "aws-apne2-research"
}

variable "instance_type" {
  description = "Collector instance. t4g.medium is the ARM candidate; t3.medium is the unverified-ARM-safe fallback."
  type        = string
  default     = "t3.medium"
}

variable "architecture" {
  description = "AMI architecture paired with instance_type. ARM must pass an Amazon Linux smoke gate before selection."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "ami_id_override" {
  description = "Reviewed and pinned Amazon Linux 2023 AMI ID required before apply. Null is allowed only for local static validation."
  type        = string
  default     = null
  nullable    = true
}

variable "ebs_size_gib" {
  description = "Encrypted gp3 hot-buffer root volume size for OS, app, active raw, and compression staging."
  type        = number
  default     = 100

  validation {
    condition     = var.ebs_size_gib >= 80 && var.ebs_size_gib <= 500
    error_message = "ebs_size_gib must be between 80 and 500 GiB."
  }
}

variable "ebs_iops" {
  description = "gp3 IOPS. The default is included in base gp3 pricing."
  type        = number
  default     = 3000
}

variable "ebs_throughput_mibps" {
  description = "gp3 throughput. The default is included in base gp3 pricing."
  type        = number
  default     = 125
}

variable "archive_bucket_name" {
  description = "Globally unique S3 name. Null derives a name from project, environment, region, and the authenticated account ID."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.archive_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.archive_bucket_name))
    error_message = "archive_bucket_name must be a valid lowercase S3 bucket name."
  }
}

variable "archive_prefix" {
  description = "S3 root prefix dedicated to market-data artifacts."
  type        = string
  default     = "market-data"
}

variable "canonical_archive_class" {
  description = "Prefix class for permanent research, holdout, audit, and reproduction evidence."
  type        = string
  default     = "canonical"
}

variable "temporary_archive_class" {
  description = "Prefix class for operational/intermediate artifacts eligible for reviewed retention."
  type        = string
  default     = "temporary"
}

variable "enable_archive_lifecycle" {
  description = "Enable non-destructive canonical storage-class transitions after review."
  type        = bool
  default     = false
}

variable "standard_ia_transition_days" {
  description = "Days before finalized objects transition to Standard-IA when lifecycle is enabled."
  type        = number
  default     = 30
}

variable "glacier_transition_days" {
  description = "Days before finalized objects transition to Glacier Flexible Retrieval when lifecycle is enabled."
  type        = number
  default     = 90
}

variable "enable_temporary_expiration" {
  description = "Enable deletion lifecycle for temporary artifacts only after archive/restore and retention review."
  type        = bool
  default     = false
}

variable "temporary_expiration_days" {
  description = "Retention period for temporary S3 artifacts when explicit expiration is enabled."
  type        = number
  default     = 30

  validation {
    condition     = var.temporary_expiration_days >= 7
    error_message = "temporary_expiration_days must be at least 7 days."
  }
}

variable "disk_warning_percent" {
  description = "Disk-used warning threshold; operators investigate compression/archive lag."
  type        = number
  default     = 70
}

variable "disk_high_percent" {
  description = "Disk-used high threshold; optional work stops and verified finalization is prioritized."
  type        = number
  default     = 80
}

variable "disk_critical_percent" {
  description = "Disk-used critical threshold; block new nonessential work and fail closed without deleting unverified raw."
  type        = number
  default     = 90
}

variable "cloudwatch_log_retention_days" {
  description = "Retention for operational logs only; raw market events must never be shipped to CloudWatch Logs."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.cloudwatch_log_retention_days)
    error_message = "cloudwatch_log_retention_days must be a CloudWatch-supported retention value."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD. Created only when budget_notification_email is provided."
  type        = number
  default     = 80
}

variable "budget_warning_percent" {
  description = "Actual-spend warning percentage."
  type        = number
  default     = 50

  validation {
    condition     = var.budget_warning_percent > 0 && var.budget_warning_percent < 100
    error_message = "budget_warning_percent must be between 0 and 100."
  }
}

variable "budget_notification_email" {
  description = "Optional budget email. Null prevents creation of a budget notification resource."
  type        = string
  default     = null
  nullable    = true
}

variable "enable_termination_protection" {
  description = "Protect the collector instance from API termination during a soak."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR for the isolated collector VPC."
  type        = string
  default     = "10.91.0.0/16"
}

variable "public_subnet_cidr" {
  description = "Single public subnet CIDR. It has no inbound security-group rules."
  type        = string
  default     = "10.91.1.0/24"
}

variable "collector_epoch" {
  description = "Immutable AWS epoch name, set only after provisioning approval."
  type        = string
  default     = null
  nullable    = true
}

variable "collector_git_commit" {
  description = "Exact 40-character commit deployed to the collector."
  type        = string
  default     = null
  nullable    = true
}

variable "collector_config_fingerprint" {
  description = "SHA-256 of the non-secret canonical collector configuration."
  type        = string
  default     = null
  nullable    = true
}

variable "collector_run_id" {
  description = "Immutable run ID generated immediately before launch."
  type        = string
  default     = null
  nullable    = true
}

variable "collector_schema_version" {
  description = "Raw/manifest schema version sealed into epoch metadata."
  type        = string
  default     = "4"
}

variable "clock_source" {
  description = "Expected host wall-clock source; verify chrony before collector start."
  type        = string
  default     = "Amazon Time Sync Service 169.254.169.123"
}

variable "compression_candidate" {
  description = "Candidate only; Amazon Linux benchmark gate must pass before the 72h run."
  type        = string
  default     = "zstd-level-1"
}

variable "additional_tags" {
  description = "Additional non-secret tags."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for key in keys(var.additional_tags) : !contains([
        "ManagedBy",
        "Project",
        "Environment",
        "Repository",
        "LiveTrading",
        "AlphaReady",
      ], key)
    ])
    error_message = "additional_tags cannot override mandatory safety/provenance tags."
  }
}
