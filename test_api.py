import base64
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location(
    "api_handler", Path(__file__).parent / "api" / "handler.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.order = {
            "OrderID": "api-buy-001",
            "Symbol": "API-DEMO",
            "Side": "BUY",
            "PriceTicks": 12500,
            "QuantityLots": 10,
        }
        self.event = {
            "routeKey": "POST /orders",
            "body": json.dumps(self.order),
            "isBase64Encoded": False,
        }

        env = patch.dict(os.environ, {
            "ORDERS_QUEUE_URL": "https://example.invalid/test-queue"
        })
        env.start()
        self.addCleanup(env.stop)

        sqs = patch.object(api, "get_sqs")
        self.sqs = sqs.start().return_value
        self.addCleanup(sqs.stop)
        self.sqs.send_message.return_value = {"MessageId": "message-test"}

    def call(self):
        return api.handler(self.event, None)

    def test_valid_order_is_queued(self):
        result = self.call()
        self.assertEqual(result["statusCode"], 202)
        self.assertEqual(json.loads(result["body"])["status"], "QUEUED")

        sent = self.sqs.send_message.call_args.kwargs
        self.assertEqual(sent["MessageGroupId"], "API-DEMO")
        self.assertEqual(json.loads(sent["MessageBody"]), self.order)

    def test_cancellation_is_queued(self):
        command = {
            "Type": "CANCEL",
            "CommandID": "api-cancel-001",
            "OrderID": "api-buy-001",
            "Symbol": "API-DEMO",
        }
        self.event.update(
            routeKey="POST /cancellations",
            body=json.dumps(command),
        )

        self.assertEqual(self.call()["statusCode"], 202)
        sent = self.sqs.send_message.call_args.kwargs
        self.assertEqual(json.loads(sent["MessageBody"]), command)

    def test_invalid_quantity_is_rejected(self):
        self.order["QuantityLots"] = True
        self.event["body"] = json.dumps(self.order)

        self.assertEqual(self.call()["statusCode"], 400)
        self.sqs.send_message.assert_not_called()

    def test_invalid_json_is_rejected(self):
        self.event["body"] = "{broken"
        self.assertEqual(self.call()["statusCode"], 400)
        self.sqs.send_message.assert_not_called()

    def test_unknown_route_is_rejected(self):
        self.event["routeKey"] = "GET /unknown"
        self.assertEqual(self.call()["statusCode"], 404)
        self.sqs.send_message.assert_not_called()

    def test_large_body_is_rejected(self):
        self.event["body"] = "x" * 16385
        self.assertEqual(self.call()["statusCode"], 413)
        self.sqs.send_message.assert_not_called()

    def test_base64_body_is_supported(self):
        self.event["body"] = base64.b64encode(
            json.dumps(self.order).encode()
        ).decode()
        self.event["isBase64Encoded"] = True

        self.assertEqual(self.call()["statusCode"], 202)

    def test_queue_failure_returns_503(self):
        self.sqs.send_message.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable"}},
            "SendMessage",
        )
        self.assertEqual(self.call()["statusCode"], 503)

    def test_identical_payload_has_stable_deduplication_id(self):
        self.call()
        first = self.sqs.send_message.call_args.kwargs[
            "MessageDeduplicationId"
        ]

        self.event["body"] = json.dumps(self.order, indent=4)
        self.call()
        second = self.sqs.send_message.call_args.kwargs[
            "MessageDeduplicationId"
        ]

        self.assertEqual(first, second)

    def test_changed_payload_has_different_deduplication_id(self):
        self.call()
        first = self.sqs.send_message.call_args.kwargs[
            "MessageDeduplicationId"
        ]

        self.order["QuantityLots"] = 11
        self.event["body"] = json.dumps(self.order)
        self.call()
        second = self.sqs.send_message.call_args.kwargs[
            "MessageDeduplicationId"
        ]

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
