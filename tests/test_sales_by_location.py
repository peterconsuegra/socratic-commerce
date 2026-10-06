"""
/sales_by_location: the /daily_sales dashboard limited to one department
(state) and/or one city.

Runs in a temporary working directory (the page reads its orders from
./data) against an in-memory SQLite database; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import Option, User  # noqa: E402
from app.services.get_data import filter_by_location, load_orders, location_choices, write_orders_csv  # noqa: E402


def order(order_id, day, city, state, total, purchase_number=1, source="facebook"):
    """One order shaped like the API's JSON."""
    return {
        "order_id": order_id, "order_date": f"2026-09-{day:02d} 10:00:00", "name": "Ana", "gender": "female",
        "email": f"c{order_id}@x.com", "city": city, "state": state, "total_value": f"{total}.00",
        "sku": "pack_favorito", "utm_source": source, "purchase_number": purchase_number,
        "is_repurchase": purchase_number > 1,
    }


ORDERS = [
    order("1", 1, "BOGOTA (C/MARCA)", "Cundinamarca", 100000),
    order("2", 2, "BOGOTA (C/MARCA)", "Cundinamarca", 50000, purchase_number=2),
    order("3", 2, "CHIA (C/MARCA)", "Cundinamarca", 70000),
    order("4", 3, "MEDELLIN (ANT)", "Antioquia", 30000),
    order("5", 4, "N/A", "N/A", 9000),
]


class TempOrdersTestCase(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("data")
        for name in ("sales_by_location_orders.csv", "all_orders.csv"):
            write_orders_csv(ORDERS, os.path.join("data", name))

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)


class LocationFilterTests(TempOrdersTestCase):
    def test_choices_list_known_states_and_then_the_selected_states_cities(self):
        path = "data/sales_by_location_orders.csv"
        self.assertEqual(location_choices(path),
                         (["Antioquia", "Cundinamarca"], ["BOGOTA (C/MARCA)", "CHIA (C/MARCA)", "MEDELLIN (ANT)"]))
        self.assertEqual(location_choices(path, "Cundinamarca")[1], ["BOGOTA (C/MARCA)", "CHIA (C/MARCA)"])
        self.assertEqual(location_choices("data/missing.csv"), ([], []))

    def test_filter_keeps_only_the_location(self):
        orders = load_orders("data/sales_by_location_orders.csv")
        self.assertEqual(filter_by_location(orders, state="Cundinamarca")["order_id"].tolist(), ["1", "2", "3"])
        self.assertEqual(filter_by_location(orders, city="MEDELLIN (ANT)")["order_id"].tolist(), ["4"])
        self.assertEqual(len(filter_by_location(orders)), len(ORDERS))


class SalesByLocationPageTests(TempOrdersTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app()
        self.app.config.update(TESTING=True, ALL_ORDERS_CSV=os.path.abspath("data/all_orders.csv"))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username="operator@example.com", password_hash="unused")
        db.session.add(user)
        db.session.add_all([
            Option(meta_key="start_date_sales_by_location_orders.csv", meta_value="01/09/2026"),
            Option(meta_key="end_date_sales_by_location_orders.csv", meta_value="30/09/2026"),
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

    def get(self, query=""):
        with mock.patch("app.routes.daily.refresh_all_orders_if_needed"):
            response = self.client.get("/sales_by_location" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    @staticmethod
    def charted(html, series):
        """Sum of one series of the combined sales + repurchases chart: 0 sales, 2 repurchases."""
        call = html.split("var dailySalesAndRepurchasesChart = buildCombinedSalesRepurchasesChart(", 1)[1]
        return sum(int(v) for v in re.findall(r"value: (\d+)", re.findall(r"\[(.*?)\]", call, re.S)[series]))

    def test_every_order_without_a_filter(self):
        html = self.get()
        self.assertEqual(self.charted(html, 0), 259000)
        self.assertEqual(self.charted(html, 2), 50000)
        self.assertIn("Showing every state and city", html)

    def test_charts_only_the_selected_state_and_city(self):
        html = self.get("?state=Cundinamarca")
        self.assertEqual((self.charted(html, 0), self.charted(html, 2)), (220000, 50000))
        self.assertNotIn(">MEDELLIN (ANT)<", html)  # the city list is Cundinamarca's

        html = self.get("?state=Cundinamarca&city=CHIA%20(C/MARCA)")
        self.assertEqual((self.charted(html, 0), self.charted(html, 2)), (70000, 0))
        self.assertIn('<option value="CHIA (C/MARCA)" selected>', html)

    def test_a_city_from_another_state_is_dropped(self):
        html = self.get("?state=Antioquia&city=BOGOTA%20(C/MARCA)")
        self.assertEqual(self.charted(html, 0), 30000)
        self.assertIn('<option value="Antioquia" selected>', html)
        self.assertNotIn("selected>BOGOTA", html)

    def test_sidebar_links_the_page(self):
        self.assertIn('<a href="/sales_by_location" class="active">Sales by Location</a>', self.get())


if __name__ == "__main__":
    unittest.main()
