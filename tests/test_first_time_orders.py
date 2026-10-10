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
    COMPARE_TOP, NO_UTM_SERIES, OTHER_SERIES, TREND_SOURCES, TREND_START, compare_periods, first_orders_report,
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


def on(day, order_id, source="facebook", campaign="c", purchase_number=1, total=100000):
    """An order on a fixed date, shaped like the API's JSON."""
    return {"order_id": order_id, "order_date": f"{day} 10:00:00", "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "utm_source": source, "utm_campaign": campaign, "utm_content": "ad",
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


# Synced on Wednesday 11 March 2026 at 10:00 in Colombia, so the last complete
# day is Tuesday 10 March - whatever day the tests run.
SYNCED_AT = pd.Timestamp("2026-03-11 10:00", tz=BOGOTA).timestamp()
DATED = [
    on("2026-01-05", "1"), on("2026-01-07", "2", "google"), on("2026-01-20", "3", "google"),
    on("2026-02-03", "4"), on("2026-02-04", "5", purchase_number=2), on("2026-02-10", "6"),
    on("2026-02-15", "7", "wati"), on("2026-02-20", "8"),
    on("2026-03-02", "9"), on("2026-03-09", "10", "google"), on("2026-03-10", "11", "wati"),
    on("2026-03-11", "12"),  # the sync day itself: not complete, never counted
]


class ComparePeriodsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        self.write(DATED)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, orders):
        write_orders_csv(orders, self.path)
        os.utime(self.path, (SYNCED_AT, SYNCED_AT))

    @staticmethod
    def rows(result):
        return [(r["name"], r["a"], r["b"]) for r in result["rows"]]

    def test_month_defaults_to_the_last_complete_month_against_the_one_before(self):
        result = compare_periods(self.path, mode="month")
        self.assertEqual((result["a"]["label"], result["b"]["label"]), ("February 2026", "January 2026"))
        self.assertEqual(self.rows(result), [("facebook", 3, 1), ("wati", 1, 0), ("google", 0, 2)])
        self.assertEqual(result["kpis"]["orders"], {"a": 4, "b": 3, "pct": 1 / 3})
        self.assertEqual(result["through"], "10 Mar 2026")

    def test_a_month_under_way_meets_the_same_days_of_the_one_before(self):
        result = compare_periods(self.path, mode="month", month="2026-03")
        self.assertEqual((result["a"]["label"], result["b"]["label"]), ("1 – 10 Mar 2026 (to date)", "1 – 10 Feb 2026"))
        self.assertEqual(result["kpis"]["orders"]["a"], 3)  # 11 March is not complete yet
        self.assertEqual(result["kpis"]["orders"]["b"], 2)  # the repeat order on 4 February does not count

    def test_week_defaults_to_the_last_complete_week_against_the_one_before(self):
        result = compare_periods(self.path, mode="week")
        self.assertEqual((result["a"]["label"], result["b"]["label"]), ("2 – 8 Mar 2026", "23 Feb – 1 Mar 2026"))
        self.assertEqual(result["kpis"]["orders"], {"a": 1, "b": 0, "pct": None})

    def test_custom_periods_and_a_backwards_one(self):
        result = compare_periods(self.path, mode="custom", custom={
            "a_start": "2026-02-01", "a_end": "2026-02-28", "b_start": "2026-01-01", "b_end": "2026-01-31"})
        self.assertEqual(result["kpis"]["orders"]["a"], 4)
        self.assertEqual(result["b"]["label"], "January 2026")

        bad = compare_periods(self.path, mode="custom", custom={
            "a_start": "2026-02-28", "a_end": "2026-02-01", "b_start": "2026-01-01", "b_end": "2026-01-31"})
        self.assertIn("start", bad["error"])
        self.assertEqual(bad["rows"], [])

    def test_campaigns_beyond_the_top_ones_roll_into_one_row(self):
        self.write([on("2026-02-0%d" % (1 + i % 9), str(i), campaign=f"camp{i:02d}") for i in range(COMPARE_TOP + 2)])
        rows = compare_periods(self.path, mode="month", month="2026-02", by="campaign")["rows"]
        self.assertEqual(len(rows), COMPARE_TOP + 1)
        self.assertEqual((rows[-1]["name"], rows[-1]["a"], rows[-1].get("other")), ("2 more", 2, True))


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

    def test_the_comparison_and_the_month_keep_each_others_choice(self):
        html = self.get(f"?month={LAST}&vs=week&vs_by=campaign")
        self.assertIn("Compare periods", html)
        self.assertIn('<option value="week" selected>Week vs the week before</option>', html)
        # The month selector carries the comparison, and the comparison the month.
        self.assertIn('<input type="hidden" name="vs" value="week">', html)
        self.assertIn('<input type="hidden" name="vs_by" value="campaign">', html)
        self.assertIn(f'<input type="hidden" name="month" value="{LAST}">', html)

    def test_sales_menu_links_first_time_orders_and_repurchase_stats(self):
        html = self.get()
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/first-time-orders" class="active"', sales)
        self.assertIn('href="/stats"', sales)
        self.assertEqual(html.count('href="/stats"'), 1)


if __name__ == "__main__":
    unittest.main()
