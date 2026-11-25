"""
/new_customers_monthly: the root view (Monthly Sales) for new customers only -
each customer's first paid order, the store's purchase_number 1.

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
from app.models import User  # noqa: E402
from app.services.get_data import BOGOTA, write_orders_csv  # noqa: E402
from app.services.monthly_repurchases import (  # noqa: E402
    get_monthly_new_customers_trend,
    get_monthly_repurchases_trend,
)

CURRENT = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
LAST = CURRENT - 1


def order(order_id, month, source, purchase_number, total=100000):
    """One order shaped like the API's JSON, on the 10th of the given month."""
    return {"order_id": order_id, "order_date": f"{month.start_time:%Y-%m}-10 10:00:00", "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca", "sku": "pack_favorito",
            "product": "Pack Favorito", "utm_source": source, "utm_campaign": f"{source} campaign", "utm_content": "ad",
            "purchase_number": purchase_number,
            "is_repurchase": "N/A" if purchase_number == "N/A" else purchase_number > 1}


ORDERS = [
    # Last month: three new customers (two from facebook) and two repurchases.
    order("1", LAST, "facebook", 1),
    order("2", LAST, "facebook", 1, total=150000),
    order("3", LAST, "google", 1, total=50000),
    order("4", LAST, "wati", 2),
    order("5", LAST, "facebook", 3),
    # Two months back: one new customer and one repurchase.
    order("6", LAST - 1, "facebook", 1),
    order("7", LAST - 1, "wati", 2),
    # Not numbered yet: neither a new customer nor a repurchase.
    order("8", LAST, "facebook", "N/A"),
]


class NewCustomersTrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_counts_first_purchases_only(self):
        rows, forecast, meta = get_monthly_new_customers_trend(
            orders_csv_path=self.path, forecast_periods=3, return_meta=True)
        by_month = {r["Month"]: r for r in rows}
        self.assertEqual(by_month[str(LAST)]["New Customer Total Value"], "300000")
        self.assertEqual(by_month[str(LAST)]["Orders"], 3)
        self.assertEqual(by_month[str(LAST - 1)]["Orders"], 1)
        self.assertEqual(len(forecast), 3)
        self.assertIn("Projected New Customer Sales", forecast[0])

    def test_one_channel(self):
        rows, _ = get_monthly_new_customers_trend(orders_csv_path=self.path, forecast_periods=1,
                                                  utm_source_filter="facebook")
        last = {r["Month"]: r for r in rows}[str(LAST)]
        self.assertEqual((last["New Customer Total Value"], last["Orders"]), ("250000", 2))

    def test_repurchases_keep_their_keys(self):
        rows, forecast = get_monthly_repurchases_trend(orders_csv_path=self.path, forecast_periods=1)
        last = {r["Month"]: r for r in rows}[str(LAST)]
        self.assertEqual((last["Repurchase Total Value"], last["Orders"]), ("200000", 2))
        self.assertIn("Projected Repurchase Sales", forecast[0])


class NewCustomersPageTests(unittest.TestCase):
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
        with mock.patch("app.routes.new_customers.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_shows_new_customer_charts_by_channel(self):
        html = self.get("/new_customers_monthly")
        self.assertNotIn("alert-danger", html)
        self.assertIn("Monthly New Customers Trend &amp; Forecast", html)
        self.assertIn('id="newCustomersCountChart"', html)
        # facebook has 2 of last month's new customers (and one before), google 1.
        self.assertIn('id="newCustomersChart_facebook"', html)
        self.assertIn('id="newCustomersChart_google"', html)
        self.assertNotIn('id="newCustomersChart_wati"', html)
        self.assertIn("Ranked by number of new customers: 3", html)

    def test_sales_menu_links_the_page(self):
        html = self.get("/new_customers_monthly")
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/new_customers_monthly" class="active"', sales)


if __name__ == "__main__":
    unittest.main()
