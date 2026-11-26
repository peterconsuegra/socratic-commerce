"""
/daily_new_customers: the /daily_sales dashboard for new customers only - each
customer's first paid order, the store's purchase_number 1.

Runs in a temporary working directory (the page reads its orders from
./data) against an in-memory SQLite database; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import shutil
import tempfile
import unittest

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import Option, User  # noqa: E402
from app.services.daily_sales import build_daily_sales_dashboard_context, get_daily_sales_trend  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402

FILE = "data/daily_new_customers_orders.csv"


def order(order_id, day, total, purchase_number=1, source="facebook"):
    """One order shaped like the API's JSON, in September 2026."""
    return {
        "order_id": order_id, "order_date": f"2026-09-{day:02d} 10:00:00", "name": "Ana", "gender": "female",
        "email": f"c{order_id}@x.com", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
        "total_value": f"{total}.00", "sku": "pack_favorito", "utm_source": source,
        "purchase_number": purchase_number,
        "is_repurchase": "N/A" if purchase_number == "N/A" else purchase_number > 1,
    }


ORDERS = [
    order("1", 1, 100000),
    order("2", 1, 50000, purchase_number=2),          # repurchase: not counted
    order("3", 2, 70000, source="google"),
    order("4", 2, 30000),
    order("5", 3, 90000, purchase_number=3, source="wati"),  # repurchase: not counted
    order("6", 3, 20000, purchase_number="N/A"),     # not numbered yet: not counted
]


class TempOrdersTestCase(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("data")
        write_orders_csv(ORDERS, FILE)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)


class FirstPurchasesOnlyTests(TempOrdersTestCase):
    def test_trend_counts_first_purchases_only(self):
        rows = get_daily_sales_trend(orders_csv_path=FILE, return_forecast=False, only="first")
        self.assertEqual([(r["Date"], r["Total Orders"], r["Total Sales"]) for r in rows],
                         [("2026-09-01", 1, "100000"), ("2026-09-02", 2, "100000")])

    def test_without_the_switch_every_order_counts(self):
        rows = get_daily_sales_trend(orders_csv_path=FILE, return_forecast=False)
        self.assertEqual(sum(r["Total Orders"] for r in rows), len(ORDERS))

    def test_dashboard_pies_and_channels_are_of_new_customers(self):
        context = build_daily_sales_dashboard_context(FILE, forecast_periods=3, only="first")
        self.assertIsNone(context["error"])
        self.assertEqual(dict(zip(context["pie_labels"], context["pie_values"])),
                         {"facebook": 130000.0, "google": 70000.0})
        self.assertEqual(sorted(ch["key"] for ch in context["channel_charts"]), ["facebook", "google"])


class DailyNewCustomersPageTests(TempOrdersTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username="operator@example.com", password_hash="unused")
        db.session.add(user)
        db.session.add_all([
            Option(meta_key="start_date_daily_new_customers_orders.csv", meta_value="01/09/2026"),
            Option(meta_key="end_date_daily_new_customers_orders.csv", meta_value="30/09/2026"),
        ])
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
        super().tearDown()

    def get(self):
        response = self.client.get("/daily_new_customers")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_charts_new_customers_without_repurchase_lines(self):
        html = self.get()
        self.assertNotIn('<div class="alert alert-danger"', html)
        self.assertIn("Daily New Customer Sales Trend and Forecast", html)
        self.assertIn('id="dailySubsetChart"', html)
        self.assertIn('id="dailySubsetCountChart"', html)
        self.assertNotIn('id="dailySalesAndRepurchasesChart"', html)
        self.assertNotIn("buildCombinedSalesRepurchasesChart(\n    \"dailySalesAndRepurchasesChart\"", html)
        counts = re.search(r'label: "New Customers",\s*data: \[([^\]]*)\]', html).group(1)
        self.assertEqual([int(v) for v in counts.split(",") if v.strip()], [1, 2])
        self.assertIn('value="daily_new_customers_orders.csv"', html)

    def test_sales_menu_links_the_page(self):
        html = self.get()
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/daily_new_customers" class="active"', sales)
        self.assertIn('href="/daily_sales" class=""', sales)

    def test_without_a_file_the_page_asks_for_a_range(self):
        os.remove(FILE)
        html = self.get()
        self.assertIn("Use the date selector above to fetch data first.", html)


if __name__ == "__main__":
    unittest.main()
