output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "offline_alarm_name" {
  value = aws_cloudwatch_metric_alarm.bot_offline.alarm_name
}
