"""Bounded development matching engine; no balances or settlement."""
import hashlib
from datetime import datetime, timezone

from book_store import load_book, commit_book, encode_item
from matching import match_order_book


def execute_order(order, request_hash, table, client, trade_table_name):
    """Process a validated order using a low-level DynamoDB client."""
    existing = table.get_item(
        Key={"OrderID": order["OrderID"]},
        ConsistentRead=True,
    ).get("Item")

    if existing is not None:
        if existing.get("RequestHash") != request_hash:
            raise ValueError("OrderID already exists with different content")
        if existing.get("EngineVersion") != 1:
            raise ValueError("Legacy intake order needs explicit migration")
        return {"Duplicate": True, "TradeCount": 0}

    symbol = order["Symbol"]
    before = load_book(table, symbol)
    now = datetime.now(timezone.utc).isoformat()

    incoming = {
        **order,
        "RemainingLots": order["QuantityLots"],
        "Sequence": before["NextSequence"],
        "RequestHash": request_hash,
        "EngineVersion": 1,
        "ReceivedAt": now,
        "UpdatedAt": now,
    }

    result = match_order_book(incoming, before["Orders"])
    completed_incoming = result["IncomingOrder"]

    writes = [{
        "Put": {
            "TableName": table.name,
            "Item": encode_item(completed_incoming),
            "ConditionExpression": "attribute_not_exists(OrderID)",
        }
    }]

    originals = {
        item["OrderID"]: item
        for item in before["Orders"]
    }

    for updated in result["RestingOrders"]:
        old = originals[updated["OrderID"]]

        if updated["RemainingLots"] == old["RemainingLots"]:
            continue

        updated["UpdatedAt"] = now

        # Protect the individual order as well as the book version.
        writes.append({
            "Put": {
                "TableName": table.name,
                "Item": encode_item(updated),
                "ConditionExpression": (
                    "attribute_exists(OrderID) "
                    "AND RemainingLots = :remaining "
                    "AND #seq = :sequence "
                    "AND RequestHash = :hash "
                    "AND EngineVersion = :engine"
                ),
                "ExpressionAttributeNames": {"#seq": "Sequence"},
                "ExpressionAttributeValues": encode_item({
                    ":remaining": old["RemainingLots"],
                    ":sequence": old["Sequence"],
                    ":hash": old["RequestHash"],
                    ":engine": 1,
                }),
            }
        })

    for index, fill in enumerate(result["Fills"]):
        # IDs are stable for this incoming order and fill position.
        identity = f"{symbol}|{order['OrderID']}|{index}"
        trade_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

        trade = {
            "TradeID": trade_id,
            "Symbol": symbol,
            "BuyOrderID": fill["BuyOrderID"],
            "SellOrderID": fill["SellOrderID"],
            "PriceTicks": fill["PriceTicks"],
            "QuantityLots": fill["QuantityLots"],
            "IncomingOrderID": order["OrderID"],
            "IncomingSequence": incoming["Sequence"],
            "FillIndex": index,
            "ExecutedAt": now,
            "EngineVersion": 1,
        }

        writes.append({
            "Put": {
                "TableName": trade_table_name,
                "Item": encode_item(trade),
                "ConditionExpression": "attribute_not_exists(TradeID)",
            }
        })

    active_orders = [
        item for item in result["RestingOrders"]
        if item["RemainingLots"] > 0
    ]

    if completed_incoming["RemainingLots"] > 0:
        active_orders.append(completed_incoming)

    commit_book(
        client=client,
        table_name=table.name,
        symbol=symbol,
        expected_version=before["Version"],
        next_sequence=before["NextSequence"] + 1,
        active_orders=active_orders,
        other_writes=writes,
    )

    return {
        "Duplicate": False,
        "TradeCount": len(result["Fills"]),
        "Status": completed_incoming["Status"],
    }
