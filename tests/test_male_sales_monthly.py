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
from app.services.get_data import load_orders  # noqa: E402
from app.services.top_campaigns import campaign_window, top_utm_campaigns, top_utm_values  # noqa: E402

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


def campaign_order(order_id, month, campaign, total=100000, purchase_number=1, gender="male"):
    row = order(order_id, month, "facebook", gender, purchase_number=purchase_number, total=total)
    row["utm_campaign"] = campaign
    return row


CAMPAIGN_ORDERS = [
    campaign_order("c1", LAST, "BlackFriday", total=200000),
    campaign_order("c2", LAST, "BlackFriday", total=100000, purchase_number=2),
    campaign_order("c3", LAST - 1, "Retargeting", total=250000, purchase_number=3),
    campaign_order("c4", LAST, "  wati  ", total=50000),
    campaign_order("c5", LAST, "undefined", total=400000),   # no campaign: not ranked
    campaign_order("c6", LAST, "N/A", total=100000),         # no campaign: not ranked
    campaign_order("c7", LAST - 13, "OldCampaign", total=900000),  # before the 12 months
    campaign_order("c8", CURRENT, "ThisMonth", total=900000),       # month not complete yet
]


class TopCampaignsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(CAMPAIGN_ORDERS, self.path)
        self.orders = load_orders(self.path)
        self.start, self.end = campaign_window(pd.Timestamp.now(tz=BOGOTA).tz_localize(None))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_window_is_the_last_12_complete_months(self):
        self.assertEqual(self.start.to_period("M"), CURRENT - 12)
        self.assertEqual(self.end.to_period("M"), LAST)

    def test_ranks_named_campaigns_by_sales(self):
        top = top_utm_campaigns(self.orders, self.start, self.end, n=10)
        self.assertEqual([r["value"] for r in top["rows"]], ["BlackFriday", "Retargeting", "wati"])
        first = top["rows"][0]
        self.assertEqual((first["sales"], first["orders"], first["avg_ticket"], first["new_customers"]),
                         (300000.0, 2, 150000.0, 1))
        self.assertEqual(first["last_order"], f"{LAST.start_time:%Y-%m}-10")
        # Shares are of every male sale in the window, campaign or not (1,100,000).
        self.assertAlmostEqual(first["share"], 300000 / 1100000 * 100)
        self.assertEqual(top["no_value_sales"], 500000.0)
        self.assertEqual(top["values"], 3)

    def test_utm_content_names_its_main_campaign(self):
        rows = []
        for i, (campaign, content, total) in enumerate([
            ("Prospecting", "VideoA", 300000), ("Retargeting", "VideoA", 100000),  # same ad name, two campaigns
            ("Prospecting", "VideoB", 150000), ("Prospecting", "undefined", 999000),
        ]):
            row = campaign_order(f"x{i}", LAST, campaign, total=total)
            row["utm_content"] = content
            rows.append(row)
        write_orders_csv(rows, self.path)
        top = top_utm_values(load_orders(self.path), self.start, self.end, field="utm_content", n=10)
        self.assertEqual([(r["value"], r["sales"], r["main_campaign"]) for r in top["rows"]],
                         [("VideoA", 400000.0, "Prospecting"), ("VideoB", 150000.0, "Prospecting")])
        self.assertEqual(top["no_value_sales"], 999000.0)

    def test_n_limits_the_rows(self):
        self.assertEqual(len(top_utm_campaigns(self.orders, self.start, self.end, n=2)["rows"]), 2)


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

    def test_both_gender_pages_rank_twenty_campaigns(self):
        from app.routes.monthly_by_kind import TOP_TABLES
        self.assertEqual((TOP_TABLES["male"], TOP_TABLES["female"]), ({"utm_campaign": 20}, {"utm_campaign": 20}))
        many = [campaign_order(f"{g}{i}", LAST, f"campaign {i}", gender=g, total=100000 + i)
                for i in range(25) for g in ("male", "female")]
        write_orders_csv(many, self.app.config["ALL_ORDERS_CSV"])
        table = self.get("/male_sales_monthly").split('id="top-utm_campaign"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("<tr>") - 1, 20)
        table = self.get("/female_sales_monthly").split('id="top-utm_campaign"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("<tr>") - 1, 20)

    def test_top_campaigns_table_is_on_the_gender_pages_only(self):
        html = self.get("/male_sales_monthly")
        self.assertIn('id="top-utm_campaign"', html)
        self.assertIn("Top 20 Campaigns for Male Sales", html)
        self.assertIn("<td>facebook campaign</td>", html.replace('<td style="word-break: break-all;">', "<td>"))
        self.assertNotIn("wati campaign", html.split('id="top-utm_campaign"', 1)[1].split("</table>", 1)[0])
        self.assertNotIn("Top 20 Campaigns for Male Sales", self.get("/new_customers_monthly"))

    def test_sales_menu_links_the_page(self):
        html = self.get("/male_sales_monthly")
        sales = html.split('<div class="nav-group-label">Sales</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/male_sales_monthly" class="active"', sales)
        self.assertIn('href="/new_customers_monthly" class=""', sales)


if __name__ == "__main__":
    unittest.main()
