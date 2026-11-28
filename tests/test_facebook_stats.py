"""
/facebook_stats: daily utm_source=facebook sales, total and per utm_campaign,
and the warning for campaigns falling more than three days in a row.

In-memory SQLite and a temporary directory; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

import pandas as pd  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Option, User  # noqa: E402
from app.services.facebook_stats import build_facebook_stats, decline_streaks  # noqa: E402
from app.services.get_data import load_orders, write_orders_csv  # noqa: E402
from app.services.periods import now_bogota_naive  # noqa: E402

NOW = pd.Timestamp("2026-11-28 10:00:00")  # the window ends 2026-11-27


def day(n):
    """The Bogotá day n days before NOW's."""
    return (NOW.normalize() - pd.Timedelta(days=n)).strftime("%Y-%m-%d")


def order(order_id, campaign, when, total, source="facebook", hour="10:00:00"):
    return {"order_id": str(order_id), "order_date": f"{when} {hour}", "order_date_utc": f"{when}T15:00:00Z",
            "name": f"c{order_id}", "email": f"c{order_id}@x.com", "phone": f"300{order_id:0>7}",
            "total_value": str(total), "city": "BOGOTA (C/MARCA)", "sku": "pack_favorito", "product": "Pack Favorito",
            "gender": "female", "utm_source": source, "utm_campaign": campaign,
            "purchase_number": 1, "is_repurchase": False}


def falling(campaign, values, start_id, source="facebook"):
    """One order a day ending yesterday, with these totals oldest first."""
    n = len(values)
    return [order(start_id + i, campaign, day(n - i), v, source=source) for i, v in enumerate(values)]


ORDERS = (
    # Down on each of the last 4 days: warned.
    falling("Prospecting_2026W47", [500000, 400000, 300000, 200000, 100000], 100)
    # Down on the last 3 days only: not "more than three".
    + falling(" CreativeTest_2026W47 ", [90000, 400000, 300000, 200000, 100000], 200)
    # Not facebook: never counted, however it falls.
    + falling("IgCampaign", [500000, 400000, 300000, 200000, 100000], 300, source="ig")
    + [
        order(400, "Prospecting_2026W47", day(1), 50000, source=" Facebook "),  # same source, trimmed, any case
        order(401, "", day(10), 70000),  # no campaign: "undefined"
        order(402, "Prospecting_2026W47", day(0), 999000),  # today: not a complete day yet
        order(403, "Prospecting_2026W47", day(45), 999000),  # before the 30-day window
    ]
)


class DeclineStreakTests(unittest.TestCase):
    def test_counts_the_last_days_in_a_row_that_went_down(self):
        daily = pd.DataFrame([[5, 4, 3, 2, 1], [1, 2, 3, 4, 5], [3, 2, 2, 1, 0], [0, 0, 0, 0, 0], [5, 1, 2, 1, 0]])
        self.assertEqual(decline_streaks(daily).tolist(), [4, 0, 2, 0, 2])

    def test_a_single_day_has_no_streak(self):
        self.assertEqual(decline_streaks(pd.DataFrame([[5]])).tolist(), [0])


class FacebookStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)
        self.stats = build_facebook_stats(load_orders(self.path), 30, now=NOW)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def campaign(self, name):
        return next(c for c in self.stats["campaigns"] if c["name"] == name)

    def test_window_is_complete_days_ending_yesterday(self):
        self.assertEqual(self.stats["start_date"], day(30))
        self.assertEqual(self.stats["end_date"], day(1))
        self.assertEqual(len(self.stats["days"]), 30)

    def test_only_facebook_orders_in_the_window_count(self):
        total = self.stats["total"]
        # 1.5M + 1.09M + 50k + 70k; not ig, not today, not 45 days ago.
        self.assertEqual(total["total_sales"], 1500000 + 1090000 + 50000 + 70000)
        self.assertEqual(total["total_orders"], 12)
        names = [c["name"] for c in self.stats["campaigns"]]
        self.assertEqual(names, ["Prospecting_2026W47", "CreativeTest_2026W47", "undefined"])

    def test_campaign_series_fill_days_without_sales(self):
        c = self.campaign("Prospecting_2026W47")
        self.assertEqual(c["sales"][-5:], [500000.0, 400000.0, 300000.0, 200000.0, 150000.0])
        self.assertEqual(c["orders"][-1], 2)
        self.assertEqual(c["sales"][:25], [0.0] * 25)
        self.assertAlmostEqual(c["share"], 1550000 / 2710000 * 100)

    def test_more_than_three_falling_days_warns(self):
        c = self.campaign("Prospecting_2026W47")
        self.assertEqual(c["decline_days"], 4)
        self.assertTrue(c["declining"])
        self.assertEqual(c["decline_from"], {"date": day(5), "sales": 500000.0})
        self.assertEqual(c["decline_to"], {"date": day(1), "sales": 150000.0})
        self.assertEqual(c["decline_start_index"], 25)

    def test_three_falling_days_do_not_warn(self):
        c = self.campaign("CreativeTest_2026W47")
        self.assertEqual(c["decline_days"], 3)
        self.assertFalse(c["declining"])
        self.assertEqual([w["name"] for w in self.stats["warnings"]], ["Prospecting_2026W47"])

    def test_total_has_a_seven_day_average(self):
        total = self.stats["total"]
        last_week = total["sales"][-7:]
        self.assertAlmostEqual(total["average"][-1], sum(last_week) / 7, places=2)
        self.assertEqual(len(total["average"]), 30)

    def test_no_facebook_orders(self):
        write_orders_csv(falling("IgCampaign", [100, 50], 1, source="ig"), self.path)
        stats = build_facebook_stats(load_orders(self.path), 14, now=NOW)
        self.assertEqual(stats["campaigns"], [])
        self.assertEqual(stats["warnings"], [])
        self.assertEqual(stats["total"]["sales"], [0.0] * 14)


class FacebookStatsPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        # Relative to the real today, since the page reads the clock.
        today = now_bogota_naive().normalize()
        shift = today - NOW.normalize()
        write_orders_csv([{**o, "order_date": f"{pd.Timestamp(o['order_date']) + shift:%Y-%m-%d %H:%M:%S}"}
                          for o in ORDERS], self.path)
        self.app = create_app()
        self.app.config.update(TESTING=True, ALL_ORDERS_CSV=self.path)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username="operator@example.com", password_hash="unused")
        db.session.add(user)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        shutil.rmtree(self.tmp)

    def get(self, path="/facebook_stats"):
        with mock.patch("app.routes.facebook_stats.refresh_all_orders_if_needed"):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_shows_charts_and_the_warning(self):
        html = self.get()
        self.assertIn("Facebook Stats", html)
        self.assertIn('href="/facebook_stats"', html)
        self.assertIn('id="totalChart"', html)
        self.assertEqual(html.count('class="campaign-chart"'), 3)
        self.assertIn("1 campaign has been falling for more than 3 days in a row", html)
        self.assertIn('<a href="#campaign-1" class="fw-semibold">Prospecting_2026W47</a>', html)
        self.assertIn("Sales fell 4 days in a row", html)

    def test_days_parameter_picks_the_window(self):
        self.assertIn('aria-current="page">Last 14 days', self.get("/facebook_stats?days=14"))
        # An unknown window falls back to 30 days.
        self.assertIn('aria-current="page">Last 30 days', self.get("/facebook_stats?days=7"))
        self.assertIn('aria-current="page">Last 30 days', self.get("/facebook_stats?days=abc"))

    def test_page_in_spanish(self):
        db.session.add(Option(meta_key="site_language", meta_value="es"))
        db.session.commit()
        html = self.get()
        self.assertIn("Estadísticas de Facebook", html)
        self.assertIn("1 campaña lleva más de 3 días seguidos cayendo", html)

    def test_missing_orders_file_shows_an_error(self):
        os.remove(self.path)
        html = self.get()
        self.assertIn("alert-danger", html)


if __name__ == "__main__":
    unittest.main()
