"""Development command-submission API. Authentication is enforced by Gateway."""
import base64
import binascii
import hashlib
import json
import logging
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_sqs = None


def get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def validate(payload, route):
    if not isinstance(payload, dict):
        raise ValueError("Body must be a JSON object")

    if route == "POST /orders":
        fields = {"OrderID", "Symbol", "Side", "PriceTicks", "QuantityLots"}
        identifiers = ("OrderID", "Symbol")
    else:
        fields = {"Type", "CommandID", "OrderID", "Symbol"}
        identifiers = ("CommandID", "OrderID", "Symbol")

    if set(payload) != fields:
        raise ValueError("Missing or unexpected fields")

    for field in identifiers:
        value = payload[field]
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", value
        ):
            raise ValueError(f"Invalid {field}")

    if route == "POST /orders":
        if payload["Side"] not in ("BUY", "SELL"):
            raise ValueError("Side must be BUY or SELL")
        for field in ("PriceTicks", "QuantityLots"):
            value = payload[field]
            if type(value) is not int or not 1 <= value <= 10**12:
                raise ValueError(f"Invalid {field}")
    elif payload["Type"] != "CANCEL":
        raise ValueError("Type must be CANCEL")


def handler(event, context):
    route = event.get("routeKey")
    if route not in ("POST /orders", "POST /cancellations"):
        return response(404, {"error": "Unknown route"})

    try:
        body = event.get("body")
        if not isinstance(body, str):
            raise ValueError("A JSON body is required")
        if len(body.encode("utf-8")) > 16384:
            return response(413, {"error": "Body too large"})

        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")

        payload = json.loads(body)
        validate(payload, route)
    except (ValueError, binascii.Error):
        return response(400, {"error": "Invalid command body or fields"})

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    # Identical payload retries share a deduplication ID. Conflicting
    # payloads reach the engine, where durable ID checks reject them.
    deduplication_id = hashlib.sha256(
        f"{route}|{canonical}".encode("utf-8")
    ).hexdigest()

    try:
        result = get_sqs().send_message(
            QueueUrl=os.environ["ORDERS_QUEUE_URL"],
            MessageBody=canonical,
            MessageGroupId=payload["Symbol"],
            MessageDeduplicationId=deduplication_id,
        )
    except (BotoCoreError, ClientError):
        logger.error("Queue submission failed")
        return response(503, {
            "error": "Submission not confirmed; retry the identical command"
        })

    return response(202, {
        "status": "QUEUED",
        "OrderID": payload["OrderID"],
        "CommandID": payload.get("CommandID", payload["OrderID"]),
        "MessageId": result["MessageId"],
    })
