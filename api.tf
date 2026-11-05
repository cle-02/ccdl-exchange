resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/ccdl-exchange-api-dev"
  retention_in_days = 14
}

resource "aws_iam_role" "api" {
  name = "ccdl-exchange-api-dev"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "api" {
  name = "ccdl-exchange-api-dev"
  role = aws_iam_role.api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.orders.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      }
    ]
  })
}

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/api"
  output_path = "${path.module}/api.zip"
}

resource "aws_lambda_function" "api" {
  function_name    = "ccdl-exchange-api-dev"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  architectures    = ["x86_64"]
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  timeout          = 15
  memory_size      = 256

  environment {
    variables = {
      ORDERS_QUEUE_URL = aws_sqs_queue.orders.url
    }
  }

  depends_on = [
    aws_iam_role_policy.api,
    aws_cloudwatch_log_group.api
  ]
}

resource "aws_apigatewayv2_api" "exchange" {
  name          = "ccdl-exchange-api-dev"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.exchange.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 20000
}

resource "aws_apigatewayv2_route" "commands" {
  for_each = toset([
    "POST /orders",
    "POST /cancellations"
  ])

  api_id             = aws_apigatewayv2_api.exchange.id
  route_key          = each.value
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_lambda_permission" "api_gateway" {
  for_each = toset(["orders", "cancellations"])

  statement_id  = "AllowGateway-${each.value}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.exchange.execution_arn}/dev/POST/${each.value}"
}

resource "aws_apigatewayv2_stage" "dev" {
  api_id      = aws_apigatewayv2_api.exchange.id
  name        = "dev"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }

  depends_on = [
    aws_apigatewayv2_route.commands,
    aws_lambda_permission.api_gateway
  ]
}

output "api_base_url" {
  value = aws_apigatewayv2_stage.dev.invoke_url
}
