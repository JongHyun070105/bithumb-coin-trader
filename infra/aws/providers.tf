provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      var.additional_tags,
      {
        ManagedBy   = "terraform"
        Project     = var.project_name
        Environment = var.environment_id
        Repository  = "JongHyun070105/bithumb-coin-trader"
        LiveTrading = "disabled"
        AlphaReady  = "false"
      },
    )
  }
}
