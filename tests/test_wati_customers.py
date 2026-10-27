"""
/wati-customers: every customer with an order tagged utm_campaign=wati, and
its Meta custom audience CSV export.

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

from app import create_app, db  # noqa: E402
from app.models import User  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402


def order(order_id, email, phone, campaign, purchase_number=1, day="2026-09-01"):
    return {"order_id": order_id, "order_date": f"{day} 10:00:00", "order_date_utc": f"{day}T15:00:00Z",
            "name": email.split("@")[0], "email": email, "phone": phone, "total_value": "100000.00",
            "city": "BOGOTA (C/MARCA)", "sku": "pack_favorito", "product": "Pack Favorito",
            "gender": "female", "utm_campaign": campaign,
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    order("1", "wati@x.com", "3001112233", "wati"),
    order("2", "wati@x.com", "3001112233", "CreativeTest_01", purchase_number=2, day="2026-10-01"),
    order("3", "upper@x.com", "3002223344", " WATI "),  # trimmed, case-insensitive
    order("4", "meta@x.com", "3003334455", "CreativeTest_01"),
    order("5", "none@x.com", "3004445566", ""),
]


class WatiCustomersTests(unittest.TestCase):
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
        with mock.patch("app.routes.wati_customers.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response

    def test_lists_only_customers_with_a_wati_order(self):
        html = self.get("/wati-customers").get_data(as_text=True)
        self.assertIn("wati@x.com", html)
        self.assertIn("upper@x.com", html)
        self.assertNotIn("meta@x.com", html)
        self.assertNotIn("none@x.com", html)

    def test_customer_type_filter(self):
        html = self.get("/wati-customers?customer_type=repeat").get_data(as_text=True)
        self.assertIn("wati@x.com", html)
        self.assertNotIn("upper@x.com", html)

    def test_export_is_a_meta_audience_csv(self):
        response = self.get("/wati-customers/export.csv")
        self.assertIn("attachment; filename=wati_customers_all_customers_", response.headers["Content-Disposition"])
        lines = response.get_data(as_text=True).strip().splitlines()
        self.assertEqual(lines[0], "fn,ln,email,phone,country,ct,gen,value")
        body = "\n".join(lines[1:])
        self.assertEqual(len(lines) - 1, 2)
        # Lifetime spend counts the non-WATI order too.
        self.assertIn("Wati,,wati@x.com,+573001112233,CO,Bogota,F,200000", body)
        self.assertNotIn("meta@x.com", body)

    def test_no_wati_customers_lists_nobody(self):
        write_orders_csv([order("9", "meta@x.com", "3003334455", "CreativeTest_01")], self.app.config["ALL_ORDERS_CSV"])
        html = self.get("/wati-customers").get_data(as_text=True)
        self.assertIn("No customers found.", html)
        self.assertNotIn("meta@x.com", html)

    def test_retention_menu_links_the_page(self):
        html = self.get("/wati-customers").get_data(as_text=True)
        self.assertIn('href="/wati-customers" class="active"', html)


if __name__ == "__main__":
    unittest.main()
