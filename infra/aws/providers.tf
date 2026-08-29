provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        ManagedBy   = "terraform"
        Project     = var.project_name
        Environment = var.environment_id
        Repository  = "JongHyun070105/bithumb-coin-trader"
        LiveTrading = "disabled"
        AlphaReady  = "false"
      },
      var.additional_tags,
    )
  }
}
