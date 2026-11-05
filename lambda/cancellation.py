"""Cancellation calculations only; no AWS calls."""
from copy import deepcopy


def plan_cancellation(order, active_orders):
    if order.get("EngineVersion") != 1:
        raise ValueError("Legacy order needs explicit migration")

    updated = deepcopy(order)
    book = deepcopy(active_orders)

    matches = [
        item for item in book
        if item["OrderID"] == order["OrderID"]
    ]

    if order["Status"] in ("FILLED", "CANCELLED"):
        if matches:
            raise ValueError("Terminal order unexpectedly present in book")
        return {
            "Changed": False,
            "Order": updated,
            "ActiveOrders": book,
            "CancelledLots": 0,
        }

    if order["Status"] not in ("OPEN", "PARTIALLY_FILLED"):
        raise ValueError("Order cannot be cancelled in this state")

    if len(matches) != 1:
        raise ValueError("Active order missing or duplicated in book")

    resting = matches[0]
    for field in (
        "Symbol", "Side", "PriceTicks", "QuantityLots",
        "RemainingLots", "Sequence", "RequestHash", "EngineVersion",
        "Status",
    ):
        if resting[field] != order[field]:
            raise ValueError("Order and book state disagree")

    remaining = order["RemainingLots"]
    quantity = order["QuantityLots"]

    if not 0 < remaining <= quantity:
        raise ValueError("Invalid remaining quantity")

    updated["FilledLots"] = quantity - remaining
    updated["CancelledLots"] = remaining
    updated["RemainingLots"] = 0
    updated["Status"] = "CANCELLED"

    book = [
        item for item in book
        if item["OrderID"] != order["OrderID"]
    ]

    return {
        "Changed": True,
        "Order": updated,
        "ActiveOrders": book,
        "CancelledLots": remaining,
    }


def execute_cancel(command, command_hash, table, client):
    """Execute a validated cancellation command atomically."""
    from datetime import datetime, timezone
    from book_store import load_book, commit_book, encode_item

    receipt_key = f"CANCEL#{command['CommandID']}"

    existing = table.get_item(
        Key={"OrderID": receipt_key},
        ConsistentRead=True,
    ).get("Item")

    if existing is not None:
        if (
            existing.get("EntityType") != "CANCEL_COMMAND"
            or existing.get("CommandHash") != command_hash
        ):
            raise ValueError("CommandID already used for different content")
        return {
            "Duplicate": True,
            "Outcome": existing["Outcome"],
        }

    symbol = command["Symbol"]
    before = load_book(table, symbol)

    order = table.get_item(
        Key={"OrderID": command["OrderID"]},
        ConsistentRead=True,
    ).get("Item")

    now = datetime.now(timezone.utc).isoformat()
    active_orders = before["Orders"]
    cancelled_lots = 0

    if order is None:
        outcome = "NOT_FOUND"
        order_action = {
            "ConditionCheck": {
                "TableName": table.name,
                "Key": encode_item({"OrderID": command["OrderID"]}),
                "ConditionExpression": "attribute_not_exists(OrderID)",
            }
        }
    else:
        if order["Symbol"] != symbol:
            raise ValueError("Cancellation symbol does not match order")

        plan = plan_cancellation(order, before["Orders"])
        active_orders = plan["ActiveOrders"]
        cancelled_lots = plan["CancelledLots"]

        guard = {
            "ConditionExpression": (
                "#status = :status "
                "AND RemainingLots = :remaining "
                "AND #sequence = :sequence "
                "AND RequestHash = :hash "
                "AND EngineVersion = :engine"
            ),
            "ExpressionAttributeNames": {
                "#status": "Status",
                "#sequence": "Sequence",
            },
            "ExpressionAttributeValues": encode_item({
                ":status": order["Status"],
                ":remaining": order["RemainingLots"],
                ":sequence": order["Sequence"],
                ":hash": order["RequestHash"],
                ":engine": 1,
            }),
        }

        if plan["Changed"]:
            outcome = "CANCELLED"
            updated = plan["Order"]
            updated["UpdatedAt"] = now
            updated["CancelledAt"] = now

            order_action = {
                "Put": {
                    "TableName": table.name,
                    "Item": encode_item(updated),
                    **guard,
                }
            }
        else:
            outcome = f"ALREADY_{order['Status']}"
            order_action = {
                "ConditionCheck": {
                    "TableName": table.name,
                    "Key": encode_item({"OrderID": order["OrderID"]}),
                    **guard,
                }
            }

    receipt = {
        "OrderID": receipt_key,
        "EntityType": "CANCEL_COMMAND",
        "CommandID": command["CommandID"],
        "TargetOrderID": command["OrderID"],
        "Symbol": symbol,
        "CommandHash": command_hash,
        "Outcome": outcome,
        "CancelledLots": cancelled_lots,
        "Sequence": before["NextSequence"],
        "ProcessedAt": now,
    }

    receipt_action = {
        "Put": {
            "TableName": table.name,
            "Item": encode_item(receipt),
            "ConditionExpression": "attribute_not_exists(OrderID)",
        }
    }

    commit_book(
        client=client,
        table_name=table.name,
        symbol=symbol,
        expected_version=before["Version"],
        next_sequence=before["NextSequence"] + 1,
        active_orders=active_orders,
        other_writes=[order_action, receipt_action],
    )

    return {
        "Duplicate": False,
        "Outcome": outcome,
    }
