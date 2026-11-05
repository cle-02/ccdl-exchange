terraform {
  required_version = ">= 1.6.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "aws" {
  region = "ap-southeast-2"
  default_tags {
    tags = {
      Environment = "Dev"
      Project     = "CCDL-Exchange"
    }
  }
}

variable "enable_processing" {
  description = "Enable only after replacing and testing the fail-closed matching stub."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Disable explicitly before intentionally destroying the development tables."
  type        = bool
  default     = true
}

locals {
  function_name = "ccdl-exchange-matcher-dev"
  timeout       = 30
}

resource "aws_dynamodb_table" "orders" {
  name                        = "Exchange_OrderBook"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "OrderID"
  deletion_protection_enabled = var.deletion_protection
  attribute {
    name = "OrderID"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "trades" {
  name                        = "Exchange_TradeHistory"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "TradeID"
  deletion_protection_enabled = var.deletion_protection
  attribute {
    name = "TradeID"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_sqs_queue" "dlq" {
  name                      = "ccdl-exchange-orders-dlq-dev.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "orders" {
  name                        = "ccdl-exchange-orders-dev.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  deduplication_scope         = "messageGroup"
  fifo_throughput_limit       = "perMessageGroupId"
  visibility_timeout_seconds  = 6 * local.timeout
  message_retention_seconds   = 345600
  receive_wait_time_seconds   = 20
  sqs_managed_sse_enabled     = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.orders.arn]
  })
}

resource "aws_sqs_queue_policy" "tls" {
  for_each = {
    orders = aws_sqs_queue.orders.id
    dlq    = aws_sqs_queue.dlq.id
  }
  queue_url = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sqs:*"
      Resource  = each.key == "orders" ? aws_sqs_queue.orders.arn : aws_sqs_queue.dlq.arn
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_cloudwatch_log_group" "matcher" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "matcher" {
  name = "ccdl-exchange-matcher-dev"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "matcher" {
  name = "ccdl-exchange-matcher-dev"
  role = aws_iam_role.matcher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.orders.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:ConditionCheckItem"]
        Resource = aws_dynamodb_table.orders.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:ConditionCheckItem"]
        Resource = aws_dynamodb_table.trades.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.matcher.arn}:*"
      }
    ]
  })
}

data "archive_file" "matcher" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/matcher.zip"
}

resource "aws_lambda_function" "matcher" {
  function_name    = local.function_name
  role             = aws_iam_role.matcher.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  architectures    = ["x86_64"]
  filename         = data.archive_file.matcher.output_path
  source_code_hash = data.archive_file.matcher.output_base64sha256
  timeout          = local.timeout
  memory_size      = 256
  environment {
    variables = {
      ORDER_BOOK_TABLE    = aws_dynamodb_table.orders.name
      TRADE_HISTORY_TABLE = aws_dynamodb_table.trades.name
    }
  }
  depends_on = [aws_iam_role_policy.matcher, aws_cloudwatch_log_group.matcher]
}

resource "aws_lambda_event_source_mapping" "orders" {
  event_source_arn        = aws_sqs_queue.orders.arn
  function_name           = aws_lambda_function.matcher.arn
  enabled                 = var.enable_processing
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 2
  }
  depends_on = [aws_iam_role_policy.matcher]
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "ccdl-exchange-dlq-not-empty-dev"
  alarm_description   = "Investigate and reconcile before replay; no notification destination configured."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
}

output "orders_queue_url" {
  value = aws_sqs_queue.orders.url
}
output "orders_queue_arn" {
  value = aws_sqs_queue.orders.arn
}
output "dead_letter_queue_url" {
  value = aws_sqs_queue.dlq.url
}
output "matcher_function_name" {
  value = aws_lambda_function.matcher.function_name
}
output "order_book_table" {
  value = aws_dynamodb_table.orders.name
}
output "trade_history_table" {
  value = aws_dynamodb_table.trades.name
}
