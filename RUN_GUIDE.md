# CCDL Exchange — Development Demo

## What is implemented
- Terraform-managed AWS infrastructure in ap-southeast-2 (Sydney).
- FIFO SQS command processing, grouped by symbol.
- Price-time matching, partial fills, and order cancellation.
- Atomic DynamoDB book/order/trade writes.
- Persistent duplicate-order and cancellation-command detection.
- IAM-authenticated HTTP API.
- Local Streamlit operator dashboard.

## Limits
This is a development demo with at most 25 active orders per symbol.
It does not implement account balances, asset reservations, settlement,
or per-user order ownership. Trade history is not a double-entry ledger.
Dead-letter handling does not guarantee uninterrupted command sequencing.
Do not use it for real-money trading.

## Local setup
Use WSL Ubuntu, Python 3, AWS CLI v2, and Terraform >=1.6,<2.
Configure authorized AWS credentials locally; never put them in source.

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements-dev.txt

## Tests
    python -m unittest -v test_handler.py test_matching.py test_engine.py test_cancellation.py test_cancel_routing.py test_api.py

Expected: 46 tests pass.

## Existing deployment
Use the original working folder with its existing Terraform state.

    terraform plan

Review any proposed changes before applying them.

## Fresh deployment
The source package intentionally excludes Terraform state and local
processing settings. Never use this fresh-deployment procedure to
recreate resources already managed by your original working folder.

    terraform init
    terraform validate
    terraform plan -var='enable_processing=false' -out=deploy.tfplan
    terraform apply deploy.tfplan

Confirm the target AWS account and review the plan before applying.
Provisioning creates billable resources.

After deploying and verifying configuration, enable processing:

    echo 'enable_processing = true' > processing.auto.tfvars
    terraform plan -out=enable.tfplan
    terraform apply enable.tfplan

## API and dashboard
Get the deployed endpoint:

    terraform output -raw api_base_url

For a fresh deployment, set BASE_URL in dashboard_api.py to that endpoint.
Routes are POST /orders and POST /cancellations, using IAM/SigV4.
HTTP 202 means queued, not executed.

Start the local dashboard:

    python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501 --server.headless true

Open http://localhost:8501 in your browser.
The dashboard uses the Python backend's AWS credentials.
Keep this operator dashboard local; it has no separate user login.

## Demo
Use fresh IDs and a fresh symbol.
1. Submit SELL: 6 lots at 12400 ticks. Verify OPEN.
2. Submit BUY: 10 lots at 12500 ticks.
3. Verify seller FILLED and buyer PARTIALLY_FILLED with 4 remaining.
4. Cancel the remaining buy quantity.
5. Verify buyer CANCELLED, FilledLots=6, CancelledLots=4.
Use the same ID and identical payload only for retries.

## State and secrets
Keep the original Terraform state securely backed up.
The shareable source ZIP is not a deployment-state or database backup.
Do not share credentials, state files, saved plans, or .venv.

## Stopping
Ctrl+C stops the local dashboard only.
AWS resources remain deployed and can still incur charges.
Do not run terraform destroy unless you intend to delete the deployment.
