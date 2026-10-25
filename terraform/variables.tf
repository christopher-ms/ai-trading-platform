variable "aws_region" {
  description = "Region the bot's log group lives in."
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Address the SNS topic emails. Set it in terraform.tfvars (gitignored) so it stays out of the public repo."
  type        = string
}

variable "log_group_name" {
  description = "Log group the CloudWatch agent ships logs/trading.log to (deploy/cloudwatch-agent.json)."
  type        = string
  default     = "/trading-bot/trading.log"
}

variable "offline_after_minutes" {
  description = "Alarm when no log line has arrived for this long. The bot logs at least once per RUN_INTERVAL_MINUTES (60), market open or not. Must be a multiple of 30."
  type        = number
  default     = 90

  validation {
    condition     = var.offline_after_minutes >= 60 && var.offline_after_minutes % 30 == 0
    error_message = "offline_after_minutes must be a multiple of 30, and at least 60."
  }
}
