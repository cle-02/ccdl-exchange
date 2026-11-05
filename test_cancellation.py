import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent / "lambda"))
from cancellation import execute_cancel

decoder = TypeDeserializer()


def decode(item):
    return {key: decoder.deserialize(value) for key, value in item.items()}


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.table = Mock()
        self.table.name = "Exchange_OrderBook"
        self.client = Mock()
        self.command = {
            "Type": "CANCEL",
            "CommandID": "cancel-001",
            "OrderID": "buy-001",
            "Symbol": "CANCEL-DEMO",
        }
        self.order = {
            "OrderID": "buy-001",
            "Symbol": "CANCEL-DEMO",
            "Side": "BUY",
            "PriceTicks": 12500,
            "QuantityLots": 10,
            "RemainingLots": 4,
            "Sequence": 1,
            "RequestHash": "order-hash",
            "EngineVersion": 1,
            "Status": "PARTIALLY_FILLED",
        }

    def prepare(self, order, active_orders):
        self.table.get_item.side_effect = [
            {},  # No cancellation receipt yet.
            {"Item": {
                "EntityType": "BOOK",
                "Symbol": "CANCEL-DEMO",
                "Version": 2,
                "NextSequence": 3,
                "OrdersJSON": json.dumps(active_orders),
            }},
            {} if order is None else {"Item": order},
        ]

    def execute(self):
        return execute_cancel(
            self.command, "command-hash", self.table, self.client
        )

    def actions(self):
        return self.client.transact_write_items.call_args.kwargs[
            "TransactItems"
        ]

    def test_partial_order_is_cancelled_atomically(self):
        original = deepcopy(self.order)
        self.prepare(self.order, [self.order])

        result = self.execute()

        self.assertEqual(result["Outcome"], "CANCELLED")
        actions = self.actions()
        self.assertEqual(len(actions), 3)

        book = decode(actions[0]["Put"]["Item"])
        order = decode(actions[1]["Put"]["Item"])
        receipt = decode(actions[2]["Put"]["Item"])

        self.assertEqual(json.loads(book["OrdersJSON"]), [])
        self.assertEqual(book["Version"], 3)
        self.assertEqual(book["NextSequence"], 4)
        self.assertEqual(order["Status"], "CANCELLED")
        self.assertEqual(order["FilledLots"], 6)
        self.assertEqual(order["CancelledLots"], 4)
        self.assertEqual(order["RemainingLots"], 0)
        self.assertEqual(receipt["OrderID"], "CANCEL#cancel-001")
        self.assertEqual(receipt["Outcome"], "CANCELLED")

        for action in actions:
            write = next(iter(action.values()))
            self.assertEqual(write["TableName"], "Exchange_OrderBook")
            self.assertIn("ConditionExpression", write)

        self.assertEqual(self.order, original)

    def test_duplicate_command_does_not_write_again(self):
        self.table.get_item.return_value = {"Item": {
            "EntityType": "CANCEL_COMMAND",
            "CommandHash": "command-hash",
            "Outcome": "CANCELLED",
        }}

        self.assertEqual(self.execute(), {
            "Duplicate": True, "Outcome": "CANCELLED"
        })
        self.client.transact_write_items.assert_not_called()

    def test_conflicting_command_id_is_rejected(self):
        self.table.get_item.return_value = {"Item": {
            "EntityType": "CANCEL_COMMAND",
            "CommandHash": "different-command",
            "Outcome": "CANCELLED",
        }}

        with self.assertRaises(ValueError):
            self.execute()

        self.client.transact_write_items.assert_not_called()

    def test_filled_order_is_not_rewritten(self):
        self.order.update(Status="FILLED", RemainingLots=0)
        self.prepare(self.order, [])

        self.assertEqual(self.execute()["Outcome"], "ALREADY_FILLED")
        self.assertIn("ConditionCheck", self.actions()[1])

    def test_already_cancelled_order_is_not_rewritten(self):
        self.order.update(
            Status="CANCELLED", RemainingLots=0,
            CancelledLots=4, FilledLots=6,
        )
        self.prepare(self.order, [])

        self.assertEqual(self.execute()["Outcome"], "ALREADY_CANCELLED")
        self.assertIn("ConditionCheck", self.actions()[1])

    def test_missing_order_records_not_found(self):
        self.prepare(None, [])

        self.assertEqual(self.execute()["Outcome"], "NOT_FOUND")
        check = self.actions()[1]["ConditionCheck"]
        self.assertEqual(
            check["ConditionExpression"], "attribute_not_exists(OrderID)"
        )

    def test_inconsistent_book_is_rejected(self):
        self.prepare(self.order, [])

        with self.assertRaises(ValueError):
            self.execute()

        self.client.transact_write_items.assert_not_called()

    def test_transaction_failure_is_propagated(self):
        self.prepare(self.order, [self.order])
        self.client.transact_write_items.side_effect = ClientError(
            {"Error": {"Code": "TransactionCanceledException"}},
            "TransactWriteItems",
        )

        with self.assertRaises(ClientError):
            self.execute()


if __name__ == "__main__":
    unittest.main()
