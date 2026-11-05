import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent / "lambda"))
from engine import execute_order

decoder = TypeDeserializer()


def decode(item):
    return {key: decoder.deserialize(value) for key, value in item.items()}


def fingerprint(order):
    payload = json.dumps(order, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.table = Mock()
        self.table.name = "Exchange_OrderBook"
        self.client = Mock()
        self.order = {
            "OrderID": "buy-engine-test",
            "Symbol": "ENGINE-DEMO",
            "Side": "BUY",
            "PriceTicks": 12500,
            "QuantityLots": 10,
        }

    def execute(self):
        return execute_order(
            self.order,
            fingerprint(self.order),
            self.table,
            self.client,
            "Exchange_TradeHistory",
        )

    def writes(self):
        return self.client.transact_write_items.call_args.kwargs[
            "TransactItems"
        ]

    def test_unmatched_order_is_open(self):
        self.table.get_item.side_effect = [{}, {}]

        result = self.execute()

        self.assertEqual(result["Status"], "OPEN")
        self.assertEqual(result["TradeCount"], 0)

        actions = self.writes()
        self.assertEqual(len(actions), 2)

        book = decode(actions[0]["Put"]["Item"])
        order = decode(actions[1]["Put"]["Item"])

        self.assertEqual(book["Version"], 1)
        self.assertEqual(book["NextSequence"], 2)
        self.assertEqual(order["RemainingLots"], 10)
        self.assertEqual(order["Sequence"], 1)
        self.assertEqual(
            json.loads(book["OrdersJSON"])[0]["OrderID"],
            self.order["OrderID"],
        )

    def test_match_updates_orders_and_creates_trade(self):
        resting = {
            "OrderID": "sell-engine-test",
            "Symbol": "ENGINE-DEMO",
            "Side": "SELL",
            "PriceTicks": 12400,
            "QuantityLots": 6,
            "RemainingLots": 6,
            "Sequence": 1,
            "Status": "OPEN",
            "RequestHash": "resting-test-hash",
            "EngineVersion": 1,
        }

        self.table.get_item.side_effect = [
            {},
            {"Item": {
                "EntityType": "BOOK",
                "Symbol": "ENGINE-DEMO",
                "Version": 1,
                "NextSequence": 2,
                "OrdersJSON": json.dumps([resting]),
            }},
        ]

        result = self.execute()

        self.assertEqual(result["TradeCount"], 1)
        self.assertEqual(result["Status"], "PARTIALLY_FILLED")

        actions = self.writes()
        self.assertEqual(len(actions), 4)

        book = decode(actions[0]["Put"]["Item"])
        buy = decode(actions[1]["Put"]["Item"])
        sell = decode(actions[2]["Put"]["Item"])
        trade = decode(actions[3]["Put"]["Item"])

        self.assertEqual(buy["RemainingLots"], 4)
        self.assertEqual(sell["RemainingLots"], 0)
        self.assertEqual(sell["Status"], "FILLED")
        self.assertEqual(trade["QuantityLots"], 6)
        self.assertEqual(trade["PriceTicks"], 12400)
        self.assertEqual(
            actions[3]["Put"]["TableName"], "Exchange_TradeHistory"
        )
        self.assertEqual(len(json.loads(book["OrdersJSON"])), 1)
        self.assertEqual(
            actions[0]["Put"]["ExpressionAttributeValues"][":expected"],
            {"N": "1"},
        )

    def test_retry_of_committed_order_makes_no_transaction(self):
        # Simulate reading the order row from a completed transaction.
        self.table.get_item.return_value = {"Item": {
            "OrderID": self.order["OrderID"],
            "RequestHash": fingerprint(self.order),
            "EngineVersion": 1,
            "Status": "FILLED",
        }}

        result = self.execute()

        self.assertTrue(result["Duplicate"])
        self.client.transact_write_items.assert_not_called()
        self.table.get_item.assert_called_once()

    def test_conflicting_duplicate_is_rejected(self):
        self.table.get_item.return_value = {"Item": {
            "RequestHash": "different-content",
            "EngineVersion": 1,
        }}

        with self.assertRaises(ValueError):
            self.execute()

        self.client.transact_write_items.assert_not_called()

    def test_legacy_intake_order_is_not_silently_skipped(self):
        self.table.get_item.return_value = {"Item": {
            "RequestHash": fingerprint(self.order),
            "Status": "RECEIVED",
        }}

        with self.assertRaises(ValueError):
            self.execute()

        self.client.transact_write_items.assert_not_called()

    def test_transaction_failure_is_propagated(self):
        self.table.get_item.side_effect = [{}, {}]
        self.client.transact_write_items.side_effect = ClientError(
            {"Error": {"Code": "TransactionCanceledException"}},
            "TransactWriteItems",
        )

        with self.assertRaises(ClientError):
            self.execute()


if __name__ == "__main__":
    unittest.main()
