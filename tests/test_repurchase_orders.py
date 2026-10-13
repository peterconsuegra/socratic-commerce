"""
/repurchase-orders: the views of /first-time-orders for repurchases - every
paid order after a customer's first (the store's is_repurchase).

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
from app.services.get_data import BOGOTA, write_orders_csv  # noqa: E402
from app.services.orders_by_kind import compare_periods, orders_report  # noqa: E402

CURRENT = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
LAST = CURRENT - 1


def order(order_id, month, source, purchase_number, city="BOGOTA (C/MARCA)", total=100000):
    """One order shaped like the API's JSON, on the 10th of the given month."""
    return {"order_id": order_id, "order_date": f"{month.start_time:%Y-%m}-10 10:00:00", "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "city": city, "state": "Cundinamarca", "sku": "pack_favorito",
            "product": "Pack Favorito", "utm_source": source, "utm_campaign": f"{source} campaign", "utm_content": "ad",
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    # Last month: three repurchases (one a third purchase) and two first-time
    # orders, which belong to the other page.
    order("1", LAST, "wati", 2),
    order("2", LAST, "wati", 3, total=150000),
    order("3", LAST, "facebook", 2, city="MEDELLIN (ANT)"),
    order("4", LAST, "facebook", 1),
    order("5", LAST, "google", 1),
    # Two months back: one repurchase.
    order("6", LAST - 1, "wati", 2),
]


class RepurchaseReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_counts_repurchases_only(self):
        report = orders_report(self.path, "repeat")
        self.assertEqual(report["selected"], {"month": str(LAST), "partial": False})
        self.assertEqual(report["kpis"]["orders"], 3)
        self.assertEqual(report["kpis"]["all_orders"], 5)
        self.assertAlmostEqual(report["kpis"]["share_of_orders"], 3 / 5)
        self.assertEqual(report["kpis"]["revenue"], 350000.0)

    def test_every_view_reads_the_repurchases(self):
        report = orders_report(self.path, "repeat")
        self.assertEqual([(s["name"], s["orders"]) for s in report["breakdown"]], [("wati", 2), ("facebook", 1)])
        self.assertEqual([(c["name"], c["orders"]) for c in report["cities"]],
                         [("BOGOTA (C/MARCA)", 2), ("MEDELLIN (ANT)", 1)])
        self.assertEqual([(p["sku"], p["orders"]) for p in report["products"]], [("pack_favorito", 3)])
        counts = {s["name"]: s["counts"] for s in report["trend"]["series"]}
        self.assertEqual(counts["wati"][-3:], [1, 2, 0])  # two months back, last month, this month

    def test_the_first_time_report_reads_the_same_file_apart(self):
        report = orders_report(self.path, "first")
        self.assertEqual(report["kpis"]["orders"], 2)
        self.assertEqual([s["name"] for s in report["breakdown"]], ["facebook", "google"])

    def test_compare_two_months_of_repurchases(self):
        result = compare_periods(self.path, "repeat", mode="month", month=str(LAST))
        self.assertEqual(result["kpis"]["orders"], {"a": 3, "b": 1, "pct": 2.0})
        self.assertEqual([(r["name"], r["a"], r["b"]) for r in result["rows"]], [("wati", 2, 1), ("facebook", 1, 0)])


class RepurchaseOrdersPageTests(unittest.TestCase):
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

    def get(self, url):
        with mock.patch("app.routes.stats.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_tells_the_report_in_repurchase_terms(self):
        html = self.get(f"/repurchase-orders?month={LAST}")
        self.assertIn('<h2 class="text-center mb-1">Repurchase Orders</h2>', html)
        self.assertIn(f"Where the repurchases of {LAST} came from", html)
        self.assertIn(f"Where the returning customers of {LAST} live", html)
        self.assertIn("Repurchase revenue", html)
        self.assertIn(f"{LAST} · 3 repurchases", html)
        self.assertIn("A repurchase is a paid order from a customer who had bought before", html)
        self.assertNotIn("first-time orders", html)

    def test_forms_and_month_links_stay_on_the_page(self):
        html = self.get("/repurchase-orders")
        self.assertIn('action="/repurchase-orders#compare"', html)
        self.assertIn('<form method="get" action="/repurchase-orders" ', html)
        self.assertIn(f'href="/repurchase-orders?month={LAST}"', html)
        self.assertNotIn('action="/first-time-orders', html)

    def test_sales_menu_links_repurchase_orders(self):
        html = self.get("/repurchase-orders")
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/repurchase-orders" class="active"', sales)
        self.assertIn('href="/first-time-orders" class=""', sales)


if __name__ == "__main__":
    unittest.main()
