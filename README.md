# CCDL Exchange: development infrastructure starter

Targets WSL Ubuntu 24.04 and AWS ap-south-1. This is an infrastructure scaffold, not a completed matching engine or financial ledger. Nothing has been deployed by the author. The accompanying PCAP PDF concerns an unrelated ALM/HPC project; this scaffold follows the written CCDL requirements.

## Included

- Exchange_OrderBook: string OrderID partition key, PAY_PER_REQUEST.
- Exchange_TradeHistory: string TradeID partition key, PAY_PER_REQUEST.
- Point-in-time recovery and deletion protection on both tables; DynamoDB default encryption at rest.
- One encrypted FIFO order queue for both sides, grouped by instrument, and a FIFO dead-letter queue (DLQ).
- 30-second Lambda timeout, 180-second visibility timeout, batch size 1, concurrency capped at 2 at the event source, partial batch failure reporting.
- Scoped execution role: consume only this order queue; read/write orders; read/insert trades; write only this function's logs. No Scan, table deletion, or trade-update permissions. PutItem still needs an attribute_not_exists condition in application code to prevent overwrites.
- Four-day source retention, fourteen-day DLQ retention, five receives before redrive; TLS-only SQS policies.
- Fourteen-day CloudWatch logs and a DLQ alarm. The alarm has no notification destination; attach an SNS topic/subscription before relying on alerts.
- Environment=Dev and Project=CCDL-Exchange via provider default tags on resources that support tags. Inline policies and queue policies do not have independent tags.

Lambda's event source mapping performs the polling. The handler processes the delivered event; it must not implement a separate ReceiveMessage loop. No aws_lambda_permission is necessary for this SQS pull integration.

## Run from WSL

Install Terraform >=1.6,<2 and AWS CLI v2 using the official instructions linked below. Python 3 is needed for local tests. Unzip into the Linux filesystem, for example ~/projects/ccdl-exchange. Configure a development AWS profile with provisioning permissions; runtime IAM permissions in this project are not provisioning permissions.

```bash
cd ~/projects/ccdl-exchange
export AWS_PROFILE=ccdl-dev
export AWS_DEFAULT_REGION=ap-south-1
aws configure sso --profile ccdl-dev   # One-time setup if your organization uses IAM Identity Center
aws sso login --profile ccdl-dev
aws sts get-caller-identity
terraform init
terraform fmt -check
terraform validate
python3 -m unittest discover -p 'test_*.py'
terraform plan -out=dev.tfplan
# Inspect the plan and target account before creating billable resources:
terraform apply dev.tfplan
```

If you use another credential method, omit the SSO commands and configure that profile appropriately. Never put access keys into .tf files. The dependency lock file pins provider selections; commit it. Exact table names mean only one deployment per account/region using these names. Existing tables must be imported and reconciled rather than recreated.

State defaults to local storage. Before collaborative use, configure a separately provisioned, versioned, encrypted S3 backend with restricted access and state locking; do not commit state or plan files. This starter does not provision the backend. For S3 use_lockfile, use a Terraform version that supports it (>=1.10).

## Safe default and test message

The event source mapping is DISABLED by default. The placeholder handler returns every order as failed and never writes to DynamoDB. It deliberately does not manufacture trades or acknowledge unfinished work. Do not enable it for live orders; messages expire even while processing is disabled. If enabled with the placeholder, messages will eventually move to the DLQ.

A producer needs sqs:SendMessage on the orders_queue_arn output. Producer IAM is not created because no producer identity was supplied. Example for a separately authorized development producer:

```bash
aws sqs send-message \
  --queue-url "$(terraform output -raw orders_queue_url)" \
  --message-group-id "TEST-INSTRUMENT" \
  --message-deduplication-id "order-0001" \
  --message-body '{"OrderID":"order-0001","Symbol":"TEST-INSTRUMENT","Side":"BUY","PriceTicks":12500,"QuantityLots":10}'
```

This only verifies enqueueing. There will be no trade or order row with the stub. Use a stable command ID as the deduplication ID on retries and a new ID for a new command. Amend/cancel commands need their own command IDs even when referencing the same OrderID. Validate that the producer's MessageGroupId equals the payload symbol in the eventual handler. Do not trust client-supplied account identity or ordering timestamps.

Implement process_order in lambda/handler.py and add integration tests before enabling:

```bash
terraform plan -var='enable_processing=true' -out=enable.tfplan
# Review after implementing and testing the engine:
terraform apply enable.tfplan
```

## Matching and financial correctness requirements

1. Queue ordering: both buy and sell orders for one symbol must use the SAME MessageGroupId. Parallelism is across symbols; a hot symbol remains sequential. SQS arrival order is not necessarily the same as client timestamp order. A single symbol cannot scale just by adding Lambda concurrency.
2. Retries: FIFO deduplication is bounded, and Lambda/SQS processing can repeat. Persist command identity and payload fingerprint atomically with effects; verify duplicate payloads, reject reused IDs with conflicting content. An in-memory seen set or short-lived transaction token alone is insufficient. A crash after committing but before acknowledgement must not create another trade.
3. Atomicity: use TransactWriteItems for related order changes, deterministic trade IDs, and idempotency markers with conditional version checks. Do not acknowledge until all required effects are committed. The role grants underlying PutItem/UpdateItem/ConditionCheckItem operations used by transactions. Large multi-fill orders require a designed, recoverable continuation protocol; one transaction cannot cover arbitrary work.
4. Access model: OrderID alone supports point lookup, not efficient best-price/oldest-order lookup. Do not Scan on every message. Design the authoritative per-symbol price-time book before implementing matching. A GSI can support discovery, but eventual consistency means it cannot by itself establish authoritative best-price ordering. This scaffold intentionally adds no speculative indexes or Query permissions.
5. Durable state: Lambda memory can disappear and cannot be the authoritative book. Define recovery/replay, checkpoints, sequencing and conditional commit behavior. Adopt integer ticks/lots or precise decimal rules; avoid binary floating point for money.
6. Ledger scope: TradeHistory is an execution record, not a double-entry ledger. Account balances, holds/reservations, settlement, balanced postings, reconciliation and audit retention require an additional explicit data model. These are not implemented or claimed by these two tables.
7. DLQ ordering: this development scaffold favors isolating poison messages. Removing a failed FIFO command to the DLQ allows later commands to run, breaking strict end-to-end command order. A strict exchange needs durable sequence-gap detection and a per-symbol halt/reconciliation procedure BEFORE processing subsequent commands. A CloudWatch alarm alone is not such a mechanism. Do not blindly redrive old orders into a live market.
8. Enablement tests: duplicate delivery after commit, conflicting duplicate payload, competing fills, partial fills, insufficient funds, cancel/fill races, crash/replay, sequence gaps and DLQ recovery. Test against AWS in an isolated account; unit tests here cover only acknowledgement behavior.

## Python or C++ integration

Python: replace the stub, package pinned application dependencies with the deployment, and preserve the partial-batch response contract. The current archive contains only the handler.

C++: build a Lambda-compatible custom runtime (for example provided.al2023) or container image for the selected architecture; Ubuntu-built binaries are not automatically ABI-compatible with Lambda. Change the Terraform packaging/runtime fields accordingly. An ordinary C++ executable cannot simply replace handler.py.

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
