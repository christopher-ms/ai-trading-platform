# Email alerting for the trading bot: CloudWatch Logs -> metric filter ->
# metric -> alarm -> SNS topic -> email. Nothing here touches the bot or
# the EC2 instance; it only reads the log group the CloudWatch agent
# already ships to.

data "aws_cloudwatch_log_group" "trading" {
  name = var.log_group_name
}

# The middle layer: alarms publish to this topic, and the topic fans each
# message out to every subscription (just the one email for now).
resource "aws_sns_topic" "alerts" {
  name = "trading-bot-alerts"
}

# Stays "PendingConfirmation" until the confirmation link AWS emails to
# alert_email is clicked; until then SNS silently drops every alert.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Turns "a log line arrived" into a number. An empty pattern matches every
# event, so each line adds 1 to TradingBot/LogLines. When nothing arrives,
# nothing is emitted at all (missing data, not zero) - the alarm below
# treats that missing data as the failure signal.
resource "aws_cloudwatch_log_metric_filter" "log_lines" {
  name           = "trading-bot-log-lines"
  log_group_name = data.aws_cloudwatch_log_group.trading.name
  pattern        = ""

  metric_transformation {
    name      = "LogLines"
    namespace = "TradingBot"
    value     = "1"
    unit      = "Count"
  }
}

# Offline alarm, 24/7. run_forever() logs at least one line every cycle,
# including "Market is currently closed. Skipping this cycle." overnight
# and at weekends, so any 90-minute silence means the process is down,
# hung, or the CloudWatch agent stopped shipping.
#
# Evaluated as offline_after_minutes / 30 back-to-back 30-minute periods,
# all of which must be empty. Because each period is either empty or not,
# the email arrives somewhere between 90 and ~120 minutes after the last line.
resource "aws_cloudwatch_metric_alarm" "bot_offline" {
  alarm_name        = "trading-bot-offline"
  alarm_description = "Trading bot may be offline: no logs received in ${var.offline_after_minutes} minutes. Check `systemctl status trading-bot` on the EC2 instance, or `aws logs tail ${var.log_group_name} --since 3h --region ${var.aws_region}`."

  namespace   = "TradingBot"
  metric_name = aws_cloudwatch_log_metric_filter.log_lines.metric_transformation[0].name
  statistic   = "Sum"
  period      = 1800

  evaluation_periods  = var.offline_after_minutes / 30
  datapoints_to_alarm = var.offline_after_minutes / 30
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  # Also emails when logs resume, so a restart is visible without checking.
  ok_actions = [aws_sns_topic.alerts.arn]
}
