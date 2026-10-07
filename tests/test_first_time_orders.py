"""
/first-time-orders: where each customer's first purchase (purchase_number 1)
came from, by month and by utm_source > utm_campaign > utm_content.

Orders are written relative to the current month, so the month a test expects
by default does not depend on the day it runs. In-memory SQLite; nothing
reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import User  # noqa: E402
from app.services.first_orders import (  # noqa: E402
    NO_UTM_SERIES, OTHER_SERIES, TREND_SOURCES, TREND_START, first_orders_report,
)
from app.services.get_data import BOGOTA, write_orders_csv  # noqa: E402

CURRENT = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
LAST = CURRENT - 1


def order(order_id, month, source, campaign, content, purchase_number=1, total=100000):
    """One order shaped like the API's JSON, on the 10th of the given month."""
    return {"order_id": order_id, "order_date": f"{month.start_time:%Y-%m}-10 10:00:00", "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "utm_source": source, "utm_campaign": campaign, "utm_content": content,
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    # Last month: three from facebook (two campaigns), one through WATI, one
    # without a UTM, and a repeat order that must not count.
    order("1", LAST, "facebook", "Prospecting", "ad_a"),
    order("2", LAST, "facebook", "Prospecting", "ad_b", total=50000),
    order("3", LAST, "facebook", "Retargeting", "ad_c"),
    order("4", LAST, "wati", "wati", "N/A"),
    order("5", LAST, "undefined", "undefined", "undefined"),
    order("6", LAST, "facebook", "Prospecting", "ad_a", purchase_number=2),
    # Two months back: a source outside the named ones.
    order("7", LAST - 1, "tiktok", "tt", "clip"),
    # This month, to date.
    order("8", CURRENT, "google", "search", "kw"),
]


class FirstOrdersReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_defaults_to_the_last_complete_month_and_counts_first_orders_only(self):
        report = first_orders_report(self.path)
        self.assertEqual(report["selected"], {"month": str(LAST), "partial": False})
        self.assertEqual(report["kpis"]["orders"], 5)
        self.assertEqual(report["kpis"]["all_orders"], 6)
        self.assertEqual(report["kpis"]["revenue"], 450000.0)

    def test_breakdown_nests_source_campaign_content_largest_first(self):
        breakdown = first_orders_report(self.path)["breakdown"]
        self.assertEqual([(s["name"], s["orders"]) for s in breakdown], [("facebook", 3), ("undefined", 1), ("wati", 1)])
        facebook = breakdown[0]
        self.assertEqual([(c["name"], c["orders"]) for c in facebook["campaigns"]], [("Prospecting", 2), ("Retargeting", 1)])
        self.assertEqual([t["name"] for t in facebook["campaigns"][0]["contents"]], ["ad_a", "ad_b"])
        self.assertAlmostEqual(facebook["share"], 3 / 5)

    def test_another_month_can_be_chosen_and_a_bad_one_falls_back(self):
        self.assertEqual(first_orders_report(self.path, str(CURRENT))["breakdown"][0]["name"], "google")
        self.assertEqual(first_orders_report(self.path, "2099-13")["selected"]["month"], str(LAST))

    def test_trend_keeps_a_fixed_series_order_and_groups_the_rest(self):
        trend = first_orders_report(self.path)["trend"]
        self.assertEqual([s["name"] for s in trend["series"]], [*TREND_SOURCES, OTHER_SERIES, NO_UTM_SERIES])
        self.assertEqual(trend["months"], [str(LAST - 1), str(LAST), str(CURRENT)])
        counts = {s["name"]: s["counts"] for s in trend["series"]}
        self.assertEqual(counts["facebook"], [0, 3, 0])
        self.assertEqual(counts[OTHER_SERIES], [1, 0, 0])   # tiktok
        self.assertEqual(counts[NO_UTM_SERIES], [0, 1, 0])  # undefined


class TrendStartTests(unittest.TestCase):
    """The chart starts after UTM tracking began; older months stay selectable."""

    def test_months_before_utm_tracking_are_left_out_of_the_chart_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "all_orders.csv")
            before = TREND_START - 1
            write_orders_csv([order("1", before, "n/a", "N/A", "N/A"), *ORDERS], path)

            trend = first_orders_report(path)["trend"]
            self.assertEqual(trend["months"][0], str(TREND_START))
            self.assertEqual(sum(sum(s["counts"]) for s in trend["series"]), 7)  # every first order but the old one

            old = first_orders_report(path, str(before))
            self.assertEqual(old["selected"]["month"], str(before))
            self.assertEqual([s["name"] for s in old["breakdown"]], ["n/a"])


class FirstTimeOrdersPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, path)
        self.app = create_app()
        self.app.config.update(TESTING=True, ALL_ORDERS_CSV=path)
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

    def get(self, query=""):
        with mock.patch("app.routes.stats.refresh_all_orders_if_needed"):
            response = self.client.get("/first-time-orders" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_shows_the_chosen_months_breakdown(self):
        html = self.get(f"?month={LAST}")
        self.assertIn(f'<option value="{LAST}" selected>', html)
        self.assertIn(f"Where the first-time orders of {LAST} came from", html)
        self.assertIn("Prospecting", html)
        self.assertIn("ad_b", html)

    def test_sales_menu_links_first_time_orders_and_repurchase_stats(self):
        html = self.get()
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/first-time-orders" class="active"', sales)
        self.assertIn('href="/stats"', sales)
        self.assertEqual(html.count('href="/stats"'), 1)


if __name__ == "__main__":
    unittest.main()
