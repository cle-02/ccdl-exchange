"""Storage helpers for a bounded development order book."""
import json
import re
from decimal import Decimal

MAX_ACTIVE_ORDERS = 25


def book_key(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", symbol
    ):
        raise ValueError("Invalid symbol")
    return f"BOOK#{symbol}"


def read_counter(value):
    # DynamoDB resource reads return numbers as Decimal.
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("Counter must be an integer")
        value = int(value)

    if type(value) is not int or value < 1:
        raise ValueError("Invalid stored counter")
    return value


def load_book(table, symbol):
    response = table.get_item(
        Key={"OrderID": book_key(symbol)},
        ConsistentRead=True,
    )
    item = response.get("Item")

    if item is None:
        return {
            "Version": 0,
            "NextSequence": 1,
            "Orders": [],
        }

    if item.get("EntityType") != "BOOK":
        raise ValueError("Unexpected item at book key")
    if item.get("Symbol") != symbol:
        raise ValueError("Stored book symbol mismatch")

    orders = json.loads(item["OrdersJSON"])
    if not isinstance(orders, list):
        raise ValueError("Invalid stored book")
    if len(orders) > MAX_ACTIVE_ORDERS:
        raise ValueError("Development book capacity exceeded")

    return {
        "Version": read_counter(item["Version"]),
        "NextSequence": read_counter(item["NextSequence"]),
        "Orders": orders,
    }


def encode_item(item):
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def commit_book(
    client,
    table_name,
    symbol,
    expected_version,
    next_sequence,
    active_orders,
    other_writes,
):
    """Commit book state plus prepared DynamoDB transaction actions.

    other_writes must contain the conditional order and trade writes.
    This helper does not construct those business-level writes.
    """
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("Invalid expected version")
    if type(next_sequence) is not int or next_sequence < 1:
        raise ValueError("Invalid next sequence")
    if not isinstance(active_orders, list):
        raise ValueError("Active orders must be a list")
    if len(active_orders) > MAX_ACTIVE_ORDERS:
        raise ValueError("Development book capacity exceeded")
    if not isinstance(other_writes, list) or not other_writes:
        raise ValueError("Order/trade writes are required")
    if len(other_writes) > 99:
        raise ValueError("Too many transaction actions")

    order_ids = []
    sequences = []

    for order in active_orders:
        if order["Symbol"] != symbol:
            raise ValueError("Order belongs to another symbol")
        if type(order["RemainingLots"]) is not int:
            raise ValueError("RemainingLots must be an integer")
        if order["RemainingLots"] <= 0:
            raise ValueError("Only active orders belong in the book")

        sequence = order["Sequence"]
        if type(sequence) is not int or not 1 <= sequence < next_sequence:
            raise ValueError("Invalid active order sequence")

        order_ids.append(order["OrderID"])
        sequences.append(sequence)

    if len(order_ids) != len(set(order_ids)):
        raise ValueError("Duplicate active OrderID")
    if len(sequences) != len(set(sequences)):
        raise ValueError("Duplicate active sequence")

    orders_json = json.dumps(
        active_orders, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    # Conservative development limit, below DynamoDB's item-size limit.
    if len(orders_json.encode("utf-8")) > 250_000:
        raise ValueError("Development book size limit exceeded")

    item = {
        "OrderID": book_key(symbol),
        "EntityType": "BOOK",
        "Symbol": symbol,
        "Version": expected_version + 1,
        "NextSequence": next_sequence,
        "OrdersJSON": orders_json,
    }

    book_put = {
        "TableName": table_name,
        "Item": encode_item(item),
    }

    if expected_version == 0:
        book_put["ConditionExpression"] = "attribute_not_exists(OrderID)"
    else:
        book_put.update({
            "ConditionExpression": "#version = :expected",
            "ExpressionAttributeNames": {"#version": "Version"},
            "ExpressionAttributeValues": {
                ":expected": {"N": str(expected_version)}
            },
        })

    # Do not catch transaction errors here. The caller must retry safely
    # after re-reading state, or report the message as failed.
    return client.transact_write_items(
        TransactItems=[{"Put": book_put}, *other_writes]
    )
