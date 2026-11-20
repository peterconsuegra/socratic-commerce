"""
/wati_stats: utm_campaign=wati orders, last 30 days vs last 7 days.

In-memory SQLite; nothing reaches the store or WATI.

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
from app.models import User  # noqa: E402
from app.routes.wati_stats import build_wati_stats  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402
from app.services.periods import now_bogota_naive  # noqa: E402


def days_ago(n):
    return (now_bogota_naive().normalize() - pd.Timedelta(days=n)).strftime("%Y-%m-%d")


def order(order_id, campaign, day, total="100000.00", purchase_number=1, gender="female"):
    return {"order_id": order_id, "order_date": f"{day} 10:00:00", "order_date_utc": f"{day}T15:00:00Z",
            "name": f"c{order_id}", "email": f"c{order_id}@x.com", "phone": f"300{order_id:0>7}",
            "total_value": total, "city": "BOGOTA (C/MARCA)", "sku": "pack_favorito", "product": "Pack Favorito",
            "gender": gender, "utm_campaign": campaign,
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    # Last 7 days: 2 wati orders, 200,000 of sales, one a repurchase.
    order("1", "wati", days_ago(2)),
    order("2", " WATI ", days_ago(3), purchase_number=2, gender="male"),
    # Only in the 30-day window.
    order("3", "wati", days_ago(15), total="300000.00"),
    order("4", "wati", days_ago(20), total="200000.00"),
    # Out of both windows, or not wati.
    order("5", "wati", days_ago(45)),
    order("6", "CreativeTest_01", days_ago(1), total="999000.00"),
]


class WatiStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)
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

    def test_windows_count_only_wati_orders(self):
        stats = build_wati_stats(self.path)
        self.assertEqual(stats["long"]["total_orders"], 4)
        self.assertEqual(stats["long"]["total_sales"], 700000.0)
        self.assertEqual(stats["short"]["total_orders"], 2)
        self.assertEqual(stats["short"]["total_sales"], 200000.0)
        self.assertEqual(stats["short"]["repurchase_orders"], 1)
        self.assertEqual(stats["short"]["first_time_orders"], 1)

    def test_per_day_change(self):
        stats = build_wati_stats(self.path)
        # 200,000 / 7 per day vs 700,000 / 30 per day.
        expected = (200000 / 7 - 700000 / 30) / (700000 / 30) * 100
        self.assertAlmostEqual(stats["change"]["sales_per_day"], expected)
        self.assertAlmostEqual(stats["short"]["avg_order_value"], 100000.0)

    def test_empty_week_has_no_order_value_or_share_change(self):
        write_orders_csv([order("3", "wati", days_ago(15))], self.path)
        change = build_wati_stats(self.path)["change"]
        self.assertEqual(change["sales_per_day"], -100.0)
        self.assertIsNone(change["avg_order_value"])
        self.assertIsNone(change["repurchase_sales_percentage"])

    def test_gender_breakdown_compares_shares(self):
        rows = {r["label"]: r for r in build_wati_stats(self.path)["gender"]}
        self.assertAlmostEqual(rows["Male"]["short_share"], 50.0)
        self.assertAlmostEqual(rows["Male"]["long_share"], 100000 / 700000 * 100)

    def test_page_renders(self):
        with mock.patch("app.routes.wati_stats.refresh_all_orders_if_needed"):
            response = self.client.get("/wati_stats")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("WATI Stats", html)
        self.assertIn("Last 30 days", html)
        self.assertIn("Last 7 days", html)
        self.assertIn('href="/wati_stats"', html)

    def test_requires_login(self):
        anon = self.app.test_client()
        self.assertNotEqual(anon.get("/wati_stats").status_code, 200)


if __name__ == "__main__":
    unittest.main()
