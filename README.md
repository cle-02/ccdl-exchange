# CCDL Exchange Engine

A development trade-matching demo built with Python, AWS serverless services,
Terraform, and a local Streamlit dashboard.

Project location: Manipal, India.
AWS deployment region: Asia Pacific (Sydney), `ap-southeast-2`.
Local development environment: WSL Ubuntu 24.04.

## Implemented features

- Price-time priority matching with partial and multiple fills.
- Atomic persistence of matching results using DynamoDB transactions.
- Duplicate order detection and idempotent cancellation commands.
- IAM-authenticated API endpoints for orders and cancellations.
- FIFO command queueing grouped by instrument symbol.
- Dashboard for submitting commands and viewing orders, books, and trades.
- Infrastructure managed with Terraform.
- Git version history and a private GitHub repository.
- GitHub Actions checks for Python tests and Terraform formatting and validation.

## Architecture

The local Streamlit dashboard sends signed requests to API Gateway.
An intake Lambda validates and queues commands in SQS.
The matching Lambda processes those commands and persists results in DynamoDB.
CloudWatch records execution logs.

Dashboard reads use the local operator's AWS credentials to access DynamoDB.

## Scope

This is a development demonstration, not a production financial exchange.
Trade history records executions; it is not a complete cash or asset ledger.
The project does not implement funding, settlement, margin, or market-data feeds.

Instrument symbols can represent stocks or commodities in demonstrations.
Real instruments require defined tick sizes, lot sizes, and trading rules.

## Included infrastructure

- DynamoDB Exchange_OrderBook with string partition key OrderID.
- DynamoDB Exchange_TradeHistory with string partition key TradeID.
- On-demand billing, point-in-time recovery, and deletion protection on both tables.
- Encrypted FIFO command queue and dead-letter queue, with TLS-only queue policies.
- Commands grouped by instrument symbol for FIFO processing.
- Matching Lambda with a 30-second timeout, 180-second queue visibility timeout,
  batch size 1, event-source maximum concurrency 2, and partial batch responses.
- API Gateway routes POST /orders and POST /cancellations using AWS_IAM authorization.
- Separate intake Lambda that validates commands and sends them to SQS.
- Scoped execution roles for the intake and matching functions.
- Four-day source queue retention, fourteen-day DLQ retention, and redrive after
  five receives.
- Fourteen-day CloudWatch log retention and a DLQ alarm. The alarm has no
  notification destination configured.
- Default tags Environment=Dev and Project=CCDL-Exchange on supported resources.

Lambda's SQS event source mapping polls the queue and invokes the matching
handler. API Gateway invokes the intake Lambda through explicit Lambda
permissions.

The matching engine uses conditional writes and DynamoDB transactions to
persist order changes, book versions, and trade records atomically.

## Run from WSL

Use AWS CLI v2, Python, and Terraform. CI uses Python 3.12 and
Terraform 1.17.1. Configure AWS credentials separately; never place
access keys in source files.

The current development deployment uses Sydney, ap-southeast-2.
Use an AWS profile authorized for the intended account. If using a named
profile, set AWS_PROFILE to its name before running these commands.

```bash
cd ~/projects/ccdl-exchange
export AWS_DEFAULT_REGION=ap-southeast-2
aws sts get-caller-identity
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -v -p 'test_*.py'
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out=dev.tfplan
```

For a fresh checkout, create the virtual environment with
`python3 -m venv .venv` before activating it.

Review the target account and the complete plan before deploying:

```bash
terraform apply dev.tfplan
```

Terraform uses local state in this development setup. Preserve the existing
state when managing the deployed resources. A fresh clone does not contain
that state and must not be used to recreate an existing deployment blindly.
Keep state, plans, credentials, and virtual environments out of Git.

Before team use, configure a separately provisioned remote backend with
encryption, versioning, restricted access, and state locking.

## Order processing

The Python handler implements order intake, matching, and cancellation.
The enable_processing variable controls the SQS event source mapping;
its default is false for a fresh configuration.

For an intentional development deployment with processing enabled, create
a local processing.auto.tfvars file containing:

```hcl
enable_processing = true
```

Review a new Terraform plan and apply it. This local variable file is
excluded from Git. Queued messages can expire while processing is disabled.

Commands for the same symbol use the same FIFO message group. The handler
reports failed and unprocessed records through partial batch responses.
Order retries must preserve the original OrderID and payload. Cancellation
retries must preserve the CommandID and payload.

A QUEUED API response confirms enqueueing, not completed execution.
Check the saved order state and CloudWatch logs for the result.

## Local dashboard

With the virtual environment active and AWS credentials configured:

```bash
python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Open http://localhost:8501. The dashboard runs locally and uses operator
AWS credentials. It is not a public multi-user trading application.

The displayed project location is Manipal, India; AWS resources remain
in Sydney. Prices and quantities are expressed in integer ticks and lots.

## Tests and continuous integration

```bash
python -m unittest discover -v -p 'test_*.py'
```

GitHub Actions runs the backend unit tests, Terraform formatting checks,
and Terraform validation on pushes to main and pull requests.
It also supports manual execution.

CI uses dummy AWS credential values for mocked tests. It does not deploy
resources or verify the live AWS environment. Live demonstrations have
separately exercised matching, cancellation, retries, and dashboard commands.

## Implementation language

The deployed engine uses Python. A C++ implementation is a possible future
extension and would require compatible Lambda packaging, runtime configuration,
and tests preserving the command and persistence contracts.

## Operations and cleanup

PAY_PER_REQUEST does not mean free: requests, backups, queues, Lambda, logs and alarms can incur charges. No VPC or NAT gateway is provisioned. Add queue-age, Lambda-error/throttle and DynamoDB-throttle alarms and notification routing before operational use. Load-test against account quotas; this development configuration is not a high-throughput benchmark.

For intentional teardown, export any needed data, disable protection via apply, inspect the destroy plan, then destroy. Queued messages and tables are deleted:

```bash
terraform apply -var='deletion_protection=false'
terraform plan -destroy -var='deletion_protection=false' -out=destroy.tfplan
terraform apply destroy.tfplan
```

## Official references

- Terraform installation: https://developer.hashicorp.com/terraform/install
- AWS CLI installation: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
- Lambda SQS configuration: https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html
- FIFO partial failures: https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html
- FIFO ordering: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-understanding-logic.html
- DLQ ordering limitations: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
- Transaction IAM: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html
- DynamoDB transactions: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html
- Terraform event mapping: https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_event_source_mapping
