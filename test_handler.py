import hashlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

LAMBDA_DIR = Path(__file__).parent / "lambda"
sys.path.insert(0, str(LAMBDA_DIR))

spec = importlib.util.spec_from_file_location(
    "handler_under_test", LAMBDA_DIR / "handler.py"
)
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class MatchingHandlerTests(unittest.TestCase):
    def setUp(self):
        self.order = {
            "OrderID": "handler-test",
            "Symbol": "ENGINE-DEMO",
            "Side": "BUY",
            "PriceTicks": 12500,
            "QuantityLots": 10,
        }
        self.record = {
            "messageId": "message-1",
            "body": json.dumps(self.order),
            "attributes": {"MessageGroupId": "ENGINE-DEMO"},
        }
        self.table = Mock()
        self.client = Mock()

        patches = [
            patch.dict(os.environ, {
                "TRADE_HISTORY_TABLE": "Exchange_TradeHistory"
            }),
            patch.object(handler, "get_table", return_value=self.table),
            patch.object(handler.boto3, "client", return_value=self.client),
            patch("engine.execute_order"),
        ]

        started = []
        for patcher in patches:
            started.append(patcher.start())
            self.addCleanup(patcher.stop)

        self.execute = started[-1]
        self.execute.return_value = {
            "Duplicate": False,
            "TradeCount": 0,
            "Status": "OPEN",
        }

    def run_handler(self, records=None):
        return handler.handler(
            {"Records": records if records is not None else [self.record]},
            None,
        )

    def test_success_passes_order_and_hash_to_engine(self):
        self.assertEqual(self.run_handler(), {"batchItemFailures": []})

        canonical = json.dumps(
            self.order, sort_keys=True, separators=(",", ":")
        )
        expected_hash = hashlib.sha256(canonical.encode()).hexdigest()

        self.execute.assert_called_once_with(
            order=self.order,
            request_hash=expected_hash,
            table=self.table,
            client=self.client,
            trade_table_name="Exchange_TradeHistory",
        )

    def test_committed_duplicate_is_acknowledged(self):
        self.execute.return_value = {"Duplicate": True, "TradeCount": 0}
        self.assertEqual(self.run_handler(), {"batchItemFailures": []})

    def test_engine_failure_retries_entire_unprocessed_suffix(self):
        self.execute.side_effect = RuntimeError("Simulated failure")
        second = {**self.record, "messageId": "message-2"}

        result = self.run_handler([self.record, second])

        self.assertEqual(result, {
            "batchItemFailures": [
                {"itemIdentifier": "message-1"},
                {"itemIdentifier": "message-2"},
            ]
        })
        self.execute.assert_called_once()

    def test_successful_prefix_is_not_retried(self):
        self.execute.side_effect = [
            {"Duplicate": False, "TradeCount": 0, "Status": "OPEN"},
            RuntimeError("Simulated failure"),
        ]
        records = [
            {**self.record, "messageId": f"message-{i}"}
            for i in (1, 2, 3)
        ]

        result = self.run_handler(records)

        self.assertEqual(result, {
            "batchItemFailures": [
                {"itemIdentifier": "message-2"},
                {"itemIdentifier": "message-3"},
            ]
        })
        self.assertEqual(self.execute.call_count, 2)

    def test_invalid_quantity_does_not_reach_engine(self):
        self.record["body"] = json.dumps({
            **self.order, "QuantityLots": 0
        })

        self.assertEqual(self.run_handler(), {
            "batchItemFailures": [{"itemIdentifier": "message-1"}]
        })
        self.execute.assert_not_called()

    def test_wrong_group_does_not_reach_engine(self):
        self.record["attributes"]["MessageGroupId"] = "OTHER"

        self.assertEqual(self.run_handler(), {
            "batchItemFailures": [{"itemIdentifier": "message-1"}]
        })
        self.execute.assert_not_called()

    def test_invalid_json_does_not_reach_engine(self):
        self.record["body"] = "{invalid-json"

        self.assertEqual(self.run_handler(), {
            "batchItemFailures": [{"itemIdentifier": "message-1"}]
        })
        self.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
