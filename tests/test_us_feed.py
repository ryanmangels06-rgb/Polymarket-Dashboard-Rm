import unittest
from datetime import datetime, timezone
from us_feed import amount, is_nfl_event, is_matchup, fetch_us_nfl_snapshot


class FakeMarkets:
    def bbo(self, slug):
        return {"marketData": {
            "bestBid": {"value": "0.52", "currency": "USD"},
            "bestAsk": {"value": "0.55", "currency": "USD"},
        }}


class FakeEvents:
    def list(self, params):
        return {"events": [{
            "id": 1, "title": "Buffalo Bills vs. Minnesota Vikings",
            "slug": "buf-min", "active": True,
            "startTime": "2026-11-09T20:15:00Z",
            "tags": [{"slug": "nfl", "label": "NFL"}],
            "markets": [{"slug": "bills-win", "title": "Buffalo wins",
                         "outcome": "Buffalo Bills", "active": True}],
        }]}


class FakeClient:
    events = FakeEvents()
    markets = FakeMarkets()


class FeedTests(unittest.TestCase):
    def test_money_amount(self):
        self.assertEqual(amount({"value": "0.55", "currency": "USD"}), 0.55)
        self.assertIsNone(amount({"value": "1.25"}))
        self.assertIsNone(amount(None))

    def test_nfl_filter(self):
        self.assertFalse(is_nfl_event({"title": "Will inflation rise?"}))
        self.assertTrue(is_nfl_event({"title": "Bears vs. Bucs", "tags": [{"slug": "nfl"}]}))
        self.assertTrue(is_matchup({"title": "Bears vs. Buccaneers"}))
        self.assertFalse(is_matchup({"title": "Who wins the Super Bowl?"}))

    def test_snapshot_quotes(self):
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        report = fetch_us_nfl_snapshot(days=3, client=FakeClient(), now=now)
        self.assertEqual(report["games_count"], 1)
        self.assertEqual(report["quote_count"], 1)
        self.assertEqual(report["games"][0]["markets"][0]["ask"], 0.55)
        self.assertEqual(report["exchange"], "polymarket_us")


if __name__ == "__main__":
    unittest.main()
