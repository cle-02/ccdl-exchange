import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "matching", Path(__file__).parent / "lambda" / "matching.py"
)
matching = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matching)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.buy = {
            "OrderID": "buy-demo",
            "Symbol": "TEST-INSTRUMENT",
            "Side": "BUY",
            "PriceTicks": 12500,
            "RemainingLots": 10,
        }
        self.sell = {
            "OrderID": "sell-demo",
            "Symbol": "TEST-INSTRUMENT",
            "Side": "SELL",
            "PriceTicks": 12400,
            "RemainingLots": 6,
        }

    def test_incoming_buy_partial_fill(self):
        result = matching.match_pair(self.buy, self.sell)
        self.assertEqual(result["PriceTicks"], 12400)
        self.assertEqual(result["QuantityLots"], 6)
        self.assertEqual(result["IncomingRemainingLots"], 4)
        self.assertEqual(result["RestingRemainingLots"], 0)

    def test_incoming_sell_uses_resting_buy_price(self):
        result = matching.match_pair(self.sell, self.buy)
        self.assertEqual(result["PriceTicks"], 12500)
        self.assertEqual(result["BuyOrderID"], "buy-demo")
        self.assertEqual(result["SellOrderID"], "sell-demo")
        self.assertEqual(result["IncomingRemainingLots"], 0)
        self.assertEqual(result["RestingRemainingLots"], 4)

    def test_equal_prices_and_quantities_fill_both(self):
        self.sell.update(PriceTicks=12500, RemainingLots=10)
        result = matching.match_pair(self.buy, self.sell)
        self.assertEqual(result["QuantityLots"], 10)
        self.assertEqual(result["IncomingRemainingLots"], 0)
        self.assertEqual(result["RestingRemainingLots"], 0)

    def test_non_crossing_prices_do_not_match(self):
        self.sell["PriceTicks"] = 12600
        self.assertIsNone(matching.match_pair(self.buy, self.sell))

    def test_same_side_does_not_match(self):
        self.sell["Side"] = "BUY"
        self.assertIsNone(matching.match_pair(self.buy, self.sell))

    def test_different_symbols_do_not_match(self):
        self.sell["Symbol"] = "OTHER"
        self.assertIsNone(matching.match_pair(self.buy, self.sell))

    def test_same_order_id_is_rejected(self):
        self.sell["OrderID"] = self.buy["OrderID"]
        with self.assertRaises(ValueError):
            matching.match_pair(self.buy, self.sell)

    def test_invalid_quantities_are_rejected(self):
        for quantity in (0, -1, True, 1.5, "6"):
            with self.subTest(quantity=quantity):
                invalid = {**self.sell, "RemainingLots": quantity}
                with self.assertRaises(ValueError):
                    matching.match_pair(self.buy, invalid)

    def test_inputs_are_not_modified(self):
        before_buy = self.buy.copy()
        before_sell = self.sell.copy()
        matching.match_pair(self.buy, self.sell)
        self.assertEqual(self.buy, before_buy)
        self.assertEqual(self.sell, before_sell)


if __name__ == "__main__":
    unittest.main()
