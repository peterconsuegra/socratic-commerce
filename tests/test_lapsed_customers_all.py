"""
/lapsed-customers-all: the /lapsed-customers list and WATI action without
hiding anyone - customers tagged recently or past the contact limit are listed.

In-memory SQLite; nothing reaches the store or WATI.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import pandas as pd

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import CustomerContact, User  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402

# Six months back: lapsed under the default 3-month window.
LAST_ORDER = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=6)
LAST_ORDER_UTC = f"{LAST_ORDER:%Y-%m-%dT%H:%M:%SZ}"


def order(order_id, email, phone):
    return {"order_id": order_id, "order_date": f"{LAST_ORDER:%Y-%m-%d %H:%M:%S}", "order_date_utc": LAST_ORDER_UTC,
            "name": email.split("@")[0], "email": email, "phone": phone, "total_value": "100000.00",
            "city": "BOGOTA (C/MARCA)", "sku": "pack_favorito", "product": "Pack Favorito",
            "purchase_number": 1, "is_repurchase": False}


ORDERS = [
    order("1", "recent@x.com", "3001112233"),     # tagged in WATI 5 days ago
    order("2", "exhausted@x.com", "3002223344"),  # tagged 3 times since the last order
    order("3", "fresh@x.com", "3003334455"),      # never contacted
]


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LapsedCustomersAllPageTests(unittest.TestCase):
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
        db.session.add(CustomerContact(email="recent@x.com", channel=CustomerContact.CHANNEL_WATI,
                                       label="winback_oct", sent_at=utc_now() - timedelta(days=5),
                                       last_order_at=LAST_ORDER_UTC))
        for weeks in (10, 12, 14):
            db.session.add(CustomerContact(email="exhausted@x.com", channel=CustomerContact.CHANNEL_WATI,
                                           label="winback_old", sent_at=utc_now() - timedelta(weeks=weeks),
                                           last_order_at=LAST_ORDER_UTC))
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
        with mock.patch("app.routes.lapsed.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_lapsed_customers_still_hides_the_contacted(self):
        html = self.get("/lapsed-customers")
        self.assertIn("fresh@x.com", html)
        self.assertNotIn("recent@x.com", html)
        self.assertNotIn("exhausted@x.com", html)
        self.assertIn("hidden: tagged in WATI in the last 30 days", html)

    def test_all_page_lists_everyone(self):
        html = self.get("/lapsed-customers-all")
        self.assertIn('<h2 class="text-center mb-1">All Lapsed Customers</h2>', html)
        for email in ("recent@x.com", "exhausted@x.com", "fresh@x.com"):
            self.assertIn(f'value="{email}"', html)
        self.assertNotIn("hidden:", html)

    def test_contact_filters_in_the_url_are_ignored(self):
        html = self.get("/lapsed-customers-all?exclude_contacted=30&max_contacts=1")
        self.assertIn('value="recent@x.com"', html)
        self.assertIn('value="exhausted@x.com"', html)

    def test_shows_who_was_contacted_but_not_the_hiding_controls(self):
        html = self.get("/lapsed-customers-all")
        self.assertIn(">WATI contacts</th>", html)
        self.assertIn(">Last contacted</th>", html)
        self.assertNotIn('name="exclude_contacted"', html)
        self.assertNotIn('name="max_contacts"', html)
        self.assertIn("Tag selected customers in WATI", html)

    def test_links_stay_on_the_page(self):
        html = self.get("/lapsed-customers-all")
        self.assertIn('<form method="get" action="/lapsed-customers-all"', html)
        self.assertIn('href="/lapsed-customers-all?page=1', html)

    def test_retention_menu_links_the_page(self):
        html = self.get("/lapsed-customers-all")
        menu = html.split('<div class="nav-group-label">Retention</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/lapsed-customers-all" class="active"', menu)
        self.assertIn('href="/lapsed-customers" class=""', menu)


if __name__ == "__main__":
    unittest.main()
