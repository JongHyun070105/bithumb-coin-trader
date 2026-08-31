data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "amazon_linux_2023" {
  count       = var.ami_id_override == null ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-${var.architecture}"]
  }

  filter {
    name   = "architecture"
    values = [var.architecture]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  selected_availability_zone = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
  selected_ami_id            = coalesce(var.ami_id_override, try(data.aws_ami.amazon_linux_2023[0].id, null))
  archive_bucket_name        = coalesce(var.archive_bucket_name, "${var.project_name}-${var.environment_id}-${var.region}-${data.aws_caller_identity.current.account_id}")
  collector_boundary_arn     = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/bitcoin-trader-collector-boundary"
  archive_epoch_root         = var.collector_epoch == null ? "pending-epoch" : var.collector_epoch
  canonical_archive_prefix   = "${var.archive_prefix}/${var.canonical_archive_class}/${local.archive_epoch_root}"
  temporary_archive_prefix   = "${var.archive_prefix}/${var.temporary_archive_class}/${local.archive_epoch_root}"

  disk_thresholds = {
    warning = {
      threshold   = var.disk_warning_percent
      periods     = 3
      description = "Investigate compression, upload, or retention lag"
    }
    high = {
      threshold   = var.disk_high_percent
      periods     = 2
      description = "Stop optional work and prioritize verified finalization/archive"
    }
    critical = {
      threshold   = var.disk_critical_percent
      periods     = 1
      description = "Block new nonessential work and fail closed; never delete unverified raw"
    }
  }

  provenance_tags = {
    CollectorEpoch    = coalesce(var.collector_epoch, "NOT-SEALED")
    CollectorCommit   = coalesce(var.collector_git_commit, "NOT-SEALED")
    ConfigFingerprint = coalesce(var.collector_config_fingerprint, "NOT-SEALED")
    CollectorRunId    = coalesce(var.collector_run_id, "NOT-SEALED")
    SchemaVersion     = var.collector_schema_version
    Architecture      = var.architecture
    ClockSource       = var.clock_source
  }
}

resource "aws_vpc" "collector" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-${var.environment_id}-vpc"
  }
}

resource "aws_internet_gateway" "collector" {
  vpc_id = aws_vpc.collector.id

  tags = {
    Name = "${var.project_name}-${var.environment_id}-igw"
  }
}

resource "aws_subnet" "collector" {
  vpc_id                  = aws_vpc.collector.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = local.selected_availability_zone
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-${var.environment_id}-collector"
  }
}

resource "aws_route_table" "collector" {
  vpc_id = aws_vpc.collector.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.collector.id
  }

  tags = {
    Name = "${var.project_name}-${var.environment_id}-collector"
  }
}

resource "aws_route_table_association" "collector" {
  subnet_id      = aws_subnet.collector.id
  route_table_id = aws_route_table.collector.id
}

resource "aws_security_group" "collector" {
  name        = "${var.project_name}-${var.environment_id}-collector"
  description = "No inbound access; egress-only public market data collector managed through SSM"
  vpc_id      = aws_vpc.collector.id

  # Deliberately no ingress blocks: SSH, dashboard, and all public inbound are closed.
  egress {
    # Dynamic exchange/CDN and AWS public endpoint IPs cannot be safely pinned.
    # This is restricted to TCP/443; there are still zero ingress rules.
    description = "Reviewed HTTPS egress for dynamic exchange and AWS public endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-${var.environment_id}-collector"
  }
}

resource "aws_s3_bucket" "archive" {
  bucket = local.archive_bucket_name

  tags = {
    Name         = local.archive_bucket_name
    DataClass    = "selective-research-and-soak-evidence"
    PublicAccess = "blocked"
    StorageRole  = "canonical-and-reviewed-temporary-only"
  }
}

resource "aws_s3_bucket_public_access_block" "archive" {
  bucket = aws_s3_bucket.archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "archive" {
  bucket = aws_s3_bucket.archive.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  count  = var.enable_archive_lifecycle || var.enable_temporary_expiration ? 1 : 0
  bucket = aws_s3_bucket.archive.id

  depends_on = [aws_s3_bucket_versioning.archive]

  dynamic "rule" {
    for_each = var.enable_archive_lifecycle ? [1] : []

    content {
      id     = "reviewed-canonical-transitions"
      status = "Enabled"

      filter {
        prefix = "${var.archive_prefix}/${var.canonical_archive_class}/"
      }

      transition {
        days          = var.standard_ia_transition_days
        storage_class = "STANDARD_IA"
      }

      transition {
        days          = var.glacier_transition_days
        storage_class = "GLACIER"
      }

      abort_incomplete_multipart_upload {
        days_after_initiation = 7
      }
    }
  }

  dynamic "rule" {
    for_each = var.enable_temporary_expiration ? [1] : []

    content {
      id     = "explicitly-reviewed-temporary-expiration"
      status = "Enabled"

      filter {
        prefix = "${var.archive_prefix}/${var.temporary_archive_class}/"
      }

      expiration {
        days = var.temporary_expiration_days
      }

      noncurrent_version_expiration {
        noncurrent_days = var.temporary_expiration_days
      }

      abort_incomplete_multipart_upload {
        days_after_initiation = 7
      }
    }
  }
}

data "aws_iam_policy_document" "archive_transport" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.archive.arn,
      "${aws_s3_bucket.archive.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "archive_transport" {
  bucket = aws_s3_bucket.archive.id
  policy = data.aws_iam_policy_document.archive_transport.json

  depends_on = [aws_s3_bucket_public_access_block.archive]
}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "collector" {
  name                 = "${var.project_name}-${var.environment_id}-collector"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume_role.json
  permissions_boundary = local.collector_boundary_arn

  tags = local.provenance_tags
}

data "aws_iam_policy_document" "collector_ssm_agent" {
  statement {
    sid = "SessionManagerCoreOnly"
    actions = [
      "ssm:DescribeAssociation",
      "ssm:GetDeployablePatchSnapshotForInstance",
      "ssm:GetDocument",
      "ssm:DescribeDocument",
      "ssm:GetManifest",
      "ssm:ListAssociations",
      "ssm:ListInstanceAssociations",
      "ssm:PutInventory",
      "ssm:PutComplianceItems",
      "ssm:PutConfigurePackageResult",
      "ssm:UpdateAssociationStatus",
      "ssm:UpdateInstanceAssociationStatus",
      "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage",
      "ec2messages:DeleteMessage",
      "ec2messages:FailMessage",
      "ec2messages:GetEndpoint",
      "ec2messages:GetMessages",
      "ec2messages:SendReply",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "collector_ssm_agent" {
  name   = "session-manager-agent-core"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector_ssm_agent.json
}

data "aws_iam_policy_document" "collector" {
  statement {
    sid       = "ListEpochArchive"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.archive.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "${local.canonical_archive_prefix}/*",
        "${local.temporary_archive_prefix}/*",
      ]
    }
  }

  statement {
    sid = "ReadWriteEpochArchive"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.archive.arn}/${local.canonical_archive_prefix}/*",
      "${aws_s3_bucket.archive.arn}/${local.temporary_archive_prefix}/*",
    ]
  }

  statement {
    sid       = "PublishCollectorMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["BitcoinTrader/Collector"]
    }
  }

  statement {
    sid = "WriteOperationalLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.collector.arn}:*"]
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "collector-epoch-access"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}

resource "aws_iam_instance_profile" "collector" {
  name = "${var.project_name}-${var.environment_id}-collector"
  role = aws_iam_role.collector.name
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/${var.project_name}/${var.environment_id}/collector"
  retention_in_days = var.cloudwatch_log_retention_days

  tags = {
    Content = "operational-only-no-raw-market-events"
  }
}

resource "aws_cloudwatch_metric_alarm" "writer_errors" {
  alarm_name          = "${var.project_name}-${var.environment_id}-writer-errors"
  alarm_description   = "Collector reported writer or unpersisted-event errors"
  namespace           = "BitcoinTrader/Collector"
  metric_name         = "WriterErrors"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    EnvironmentId = var.environment_id
  }
}

resource "aws_cloudwatch_metric_alarm" "queue_drops" {
  alarm_name          = "${var.project_name}-${var.environment_id}-queue-drops"
  alarm_description   = "Collector reported queue drops or backpressure loss"
  namespace           = "BitcoinTrader/Collector"
  metric_name         = "QueueDrops"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    EnvironmentId = var.environment_id
  }
}

resource "aws_cloudwatch_metric_alarm" "disk_used" {
  for_each = local.disk_thresholds

  alarm_name          = "${var.project_name}-${var.environment_id}-disk-${each.key}"
  alarm_description   = each.value.description
  namespace           = "BitcoinTrader/Collector"
  metric_name         = "DiskUsedPercent"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = each.value.periods
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    EnvironmentId = var.environment_id
  }
}

resource "aws_instance" "collector" {
  ami                         = local.selected_ami_id
  instance_type               = var.instance_type
  availability_zone           = local.selected_availability_zone
  subnet_id                   = aws_subnet.collector.id
  vpc_security_group_ids      = [aws_security_group.collector.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.collector.name
  disable_api_termination     = var.enable_termination_protection
  # Avoid paid EC2 detailed monitoring; collector metrics remain explicit,
  # standard-resolution custom metrics in BitcoinTrader/Collector.
  monitoring = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = var.ebs_size_gib
    iops                  = var.ebs_iops
    throughput            = var.ebs_throughput_mibps
    delete_on_termination = false

    tags = merge(local.provenance_tags, {
      Name      = "${var.project_name}-${var.environment_id}-collector-data"
      DataClass = "hot-buffer-active-and-staging"
    })
  }

  tags = merge(local.provenance_tags, {
    Name            = "${var.project_name}-${var.environment_id}-collector"
    AvailabilityAZ  = local.selected_availability_zone
    InstanceType    = var.instance_type
    CompressionMode = var.compression_candidate
  })

  lifecycle {
    precondition {
      condition     = var.ami_id_override != null && can(regex("^ami-[0-9a-f]+$", var.ami_id_override))
      error_message = "ami_id_override must be a reviewed, pinned Amazon Linux AMI ID before apply."
    }

    precondition {
      condition     = var.collector_epoch != null && !contains(["", "NOT-SEALED"], var.collector_epoch)
      error_message = "collector_epoch must be sealed before a provider-backed plan."
    }

    precondition {
      condition     = var.collector_git_commit != null && can(regex("^[0-9a-f]{40}$", var.collector_git_commit))
      error_message = "collector_git_commit must be the exact 40-character lowercase commit SHA."
    }

    precondition {
      condition     = var.collector_config_fingerprint != null && can(regex("^[0-9a-f]{64}$", var.collector_config_fingerprint))
      error_message = "collector_config_fingerprint must be a 64-character lowercase SHA-256."
    }

    precondition {
      condition     = var.collector_run_id != null && !contains(["", "NOT-SEALED"], var.collector_run_id)
      error_message = "collector_run_id must be generated and sealed before a provider-backed plan."
    }

    precondition {
      condition     = var.availability_zone != null && var.availability_zone != ""
      error_message = "availability_zone must be explicitly selected and sealed before a provider-backed plan."
    }

    precondition {
      condition     = !startswith(var.instance_type, "t4g.") || var.architecture == "arm64"
      error_message = "t4g instance types require architecture=arm64."
    }

    precondition {
      condition     = !startswith(var.instance_type, "t3.") || var.architecture == "x86_64"
      error_message = "t3 instance types require architecture=x86_64."
    }

    precondition {
      condition = (
        var.disk_warning_percent > 0 &&
        var.disk_warning_percent < var.disk_high_percent &&
        var.disk_high_percent < var.disk_critical_percent &&
        var.disk_critical_percent < 100
      )
      error_message = "Disk thresholds must satisfy 0 < warning < high < critical < 100."
    }
  }

  depends_on = [
    aws_iam_role_policy.collector,
    aws_iam_role_policy.collector_ssm_agent,
    aws_route_table_association.collector,
  ]
}

resource "aws_budgets_budget" "monthly" {
  count = var.budget_notification_email == null ? 0 : 1

  name         = "${var.project_name}-${var.environment_id}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.budget_warning_percent
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
