import unittest
from decimal import Decimal

from pricing import average_price, quote


class AveragePrice(unittest.TestCase):
    def test_weighted(self):
        self.assertEqual(average_price([{"amount": 1, "price": 10}, {"amount": 3, "price": 20}]), Decimal("17.5"))

    def test_quote(self):
        self.assertEqual(quote("KGA", [{"amount": 2, "price": 5}])["price"], "5")

    def test_no_trades(self):
        # A token with no trades in the window has no price yet; the endpoint must not crash.
        self.assertIsNone(quote("KGA", [])["price"])


if __name__ == "__main__":
    unittest.main()
