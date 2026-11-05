import hashlib
import json
import unittest
from unittest.mock import patch

from test_handler import handler


class CancelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.command = {
            "Type": "CANCEL",
            "CommandID": "cancel-routing-test",
            "OrderID": "buy-routing-test",
            "Symbol": "CANCEL-DEMO",
        }
        self.record = {
            "messageId": "cancel-message",
            "body": json.dumps(self.command),
            "attributes": {"MessageGroupId": "CANCEL-DEMO"},
        }

        patches = {
            "cancel": patch("cancellation.execute_cancel"),
            "match": patch("engine.execute_order"),
            "table": patch.object(handler, "get_table"),
            "client": patch.object(handler.boto3, "client"),
        }

        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

        self.cancel.return_value = {
            "Duplicate": False,
            "Outcome": "CANCELLED",
        }

    def run_handler(self):
        return handler.handler({"Records": [self.record]}, None)

    def assert_failed(self):
        self.assertEqual(self.run_handler(), {
            "batchItemFailures": [{"itemIdentifier": "cancel-message"}]
        })

    def test_cancel_is_routed_with_correct_hash(self):
        self.assertEqual(self.run_handler(), {"batchItemFailures": []})

        canonical = json.dumps(
            self.command, sort_keys=True, separators=(",", ":")
        )
        expected_hash = hashlib.sha256(canonical.encode()).hexdigest()

        self.cancel.assert_called_once_with(
            command=self.command,
            command_hash=expected_hash,
            table=self.table.return_value,
            client=self.client.return_value,
        )
        self.match.assert_not_called()

    def test_duplicate_cancel_is_acknowledged(self):
        self.cancel.return_value = {
            "Duplicate": True,
            "Outcome": "CANCELLED",
        }
        self.assertEqual(self.run_handler(), {"batchItemFailures": []})

    def test_cancel_failure_is_retried(self):
        self.cancel.side_effect = RuntimeError("Simulated failure")
        self.assert_failed()

    def test_wrong_group_is_rejected(self):
        self.record["attributes"]["MessageGroupId"] = "OTHER"
        self.assert_failed()
        self.cancel.assert_not_called()

    def test_unknown_command_type_is_rejected(self):
        self.command["Type"] = "UNKNOWN"
        self.record["body"] = json.dumps(self.command)
        self.assert_failed()
        self.cancel.assert_not_called()
        self.match.assert_not_called()

    def test_missing_command_id_is_rejected(self):
        del self.command["CommandID"]
        self.record["body"] = json.dumps(self.command)
        self.assert_failed()
        self.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
