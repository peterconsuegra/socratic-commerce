"""
/male_sales_monthly: the root view (Monthly Sales) for the orders the store
marks male - every purchase, first or repeat; "unknown" gender never counts.
Shares routes/monthly_by_kind.py and templates/monthly_by_kind.html with
/new_customers_monthly.

Orders are written relative to the current month, so the months a test expects
do not depend on the day it runs. In-memory SQLite; nothing reaches the store.

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
from app.i18n import SPANISH  # noqa: E402
from app.models import User  # noqa: E402
from app.routes.monthly_by_kind import KIND_TEXT  # noqa: E402
from app.services.get_data import BOGOTA, write_orders_csv  # noqa: E402
from app.services.monthly_repurchases import get_monthly_male_sales_trend  # noqa: E402

CURRENT = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
LAST = CURRENT - 1


def order(order_id, month, source, gender, purchase_number=1, total=100000):
    """One order shaped like the API's JSON, on the 10th of the given month."""
    return {"order_id": order_id, "order_date": f"{month.start_time:%Y-%m}-10 10:00:00", "email": f"c{order_id}@x.com",
            "gender": gender, "total_value": f"{total}.00", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
            "sku": "pack_favorito", "product": "Pack Favorito", "utm_source": source,
            "utm_campaign": f"{source} campaign", "utm_content": "ad",
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    # Last month: three male orders (one a repurchase), one female, one unknown.
    order("1", LAST, "facebook", "male"),
    order("2", LAST, "facebook", "male", purchase_number=2, total=150000),
    order("3", LAST, "google", "male", total=50000),
    order("4", LAST, "facebook", "female"),
    order("5", LAST, "wati", "unknown"),
    # Two months back: one male order.
    order("6", LAST - 1, "facebook", "male"),
]


class MaleSalesTrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_counts_male_orders_only(self):
        rows, forecast = get_monthly_male_sales_trend(orders_csv_path=self.path, forecast_periods=2)
        by_month = {r["Month"]: r for r in rows}
        self.assertEqual((by_month[str(LAST)]["Male Total Value"], by_month[str(LAST)]["Orders"]), ("300000", 3))
        self.assertEqual(by_month[str(LAST - 1)]["Orders"], 1)
        self.assertIn("Projected Male Sales", forecast[0])

    def test_one_channel(self):
        rows, _ = get_monthly_male_sales_trend(orders_csv_path=self.path, forecast_periods=1,
                                               utm_source_filter="facebook")
        last = {r["Month"]: r for r in rows}[str(LAST)]
        self.assertEqual((last["Male Total Value"], last["Orders"]), ("250000", 2))


class KindTextTests(unittest.TestCase):
    def test_every_kind_text_has_spanish(self):
        missing = [v for t in KIND_TEXT.values() for k, v in t.items() if k != "chart_prefix" and v not in SPANISH]
        self.assertEqual(missing, [])


class MaleSalesPageTests(unittest.TestCase):
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
        with mock.patch("app.routes.monthly_by_kind.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_shows_male_charts_by_channel(self):
        html = self.get("/male_sales_monthly")
        self.assertNotIn('<div class="alert alert-danger"', html)
        self.assertIn("Monthly Male Sales Trend &amp; Forecast", html)
        self.assertIn('id="maleSalesCountChart"', html)
        self.assertIn('id="maleSalesChart_facebook"', html)
        self.assertIn('id="maleSalesChart_google"', html)
        self.assertNotIn('id="maleSalesChart_wati"', html)  # the unknown-gender order
        self.assertIn("Ranked by number of male orders: 3", html)
        self.assertNotIn("New Customer", html.split("<!-- Main Content Block -->", 1)[1])

    def test_sales_menu_links_the_page(self):
        html = self.get("/male_sales_monthly")
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/male_sales_monthly" class="active"', sales)
        self.assertIn('href="/new_customers_monthly" class=""', sales)


if __name__ == "__main__":
    unittest.main()
