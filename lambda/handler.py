"""Development matching handler. No balances or settlement."""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_table = None


def get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(
            os.environ["ORDER_BOOK_TABLE"]
        )
    return _table


def validate_order(record):
    order = json.loads(record["body"])
    fields = {"OrderID", "Symbol", "Side", "PriceTicks", "QuantityLots"}

    if not isinstance(order, dict) or set(order) != fields:
        raise ValueError("Order must contain exactly the required fields")

    for field in ("OrderID", "Symbol"):
        value = order[field]
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", value
        ):
            raise ValueError(f"Invalid {field}")

    if order["Side"] not in ("BUY", "SELL"):
        raise ValueError("Side must be BUY or SELL")

    for field in ("PriceTicks", "QuantityLots"):
        value = order[field]
        if type(value) is not int or not 1 <= value <= 10**12:
            raise ValueError(f"{field} must be a positive bounded integer")

    if record.get("attributes", {}).get("MessageGroupId") != order["Symbol"]:
        raise ValueError("MessageGroupId must match Symbol")

    return order


def validate_cancel(record):
    command = json.loads(record["body"])
    fields = {"Type", "CommandID", "OrderID", "Symbol"}

    if not isinstance(command, dict) or set(command) != fields:
        raise ValueError("Invalid cancellation fields")
    if command["Type"] != "CANCEL":
        raise ValueError("Unsupported command type")

    for field in ("CommandID", "OrderID", "Symbol"):
        value = command[field]
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", value
        ):
            raise ValueError(f"Invalid {field}")

    if record.get("attributes", {}).get("MessageGroupId") != command["Symbol"]:
        raise ValueError("MessageGroupId must match Symbol")

    return command


def process_order(record):
    from engine import execute_order
    from cancellation import execute_cancel

    payload = json.loads(record["body"])
    if not isinstance(payload, dict):
        raise ValueError("Message body must be an object")

    if "Type" in payload:
        command = validate_cancel(record)
        canonical = json.dumps(
            command, sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        result = execute_cancel(
            command=command,
            command_hash=fingerprint,
            table=get_table(),
            client=boto3.client("dynamodb"),
        )

        logger.info(
            "Cancellation processed: outcome=%s duplicate=%s",
            result["Outcome"],
            result["Duplicate"],
        )
        return

    order = validate_order(record)
    canonical = json.dumps(order, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    result = execute_order(
        order=order,
        request_hash=fingerprint,
        table=get_table(),
        client=boto3.client("dynamodb"),
        trade_table_name=os.environ["TRADE_HISTORY_TABLE"],
    )

    if result["Duplicate"]:
        logger.info("Previously committed matching order verified")
    else:
        logger.info(
            "Matching committed: status=%s trades=%s",
            result["Status"],
            result["TradeCount"],
        )


def handler(event, context):
    records = event["Records"]

    for index, record in enumerate(records):
        try:
            process_order(record)
        except Exception as exc:
            # Log the exception type, not the order body or customer data.
            logger.error("Command processing failed: %s", type(exc).__name__)

            # Preserve FIFO ordering: retry the failed record and everything
            # in this batch that has not yet been processed.
            return {
                "batchItemFailures": [
                    {"itemIdentifier": item["messageId"]}
                    for item in records[index:]
                ]
            }

    return {"batchItemFailures": []}
