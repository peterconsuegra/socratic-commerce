"""
/link_in_bio_stats_monthly and /link_in_bio_stats_daily: the root view and
/daily_sales for the orders from Instagram's link in bio - utm_content
link_in_bio (trimmed, any case).

The monthly page reads all_orders.csv; the daily page its own date-range file
under ./data, so these tests run in a temporary working directory against an
in-memory SQLite database. Nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.i18n import SPANISH  # noqa: E402
from app.models import Option, User  # noqa: E402
from app.routes.daily_by_subset import SUBSET_TEXT  # noqa: E402
from app.services.daily_sales import build_daily_sales_dashboard_context, get_daily_sales_trend  # noqa: E402
from app.services.get_data import BOGOTA, write_orders_csv  # noqa: E402
from app.services.monthly_repurchases import get_monthly_link_in_bio_trend  # noqa: E402

CURRENT = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
LAST = CURRENT - 1
DAILY_FILE = "data/link_in_bio_daily_orders.csv"


def order(order_id, when, content, source="ig", total=100000, purchase_number=1, campaign="undefined"):
    """One order shaped like the API's JSON; when is a 'YYYY-MM-DD' day."""
    return {"order_id": order_id, "order_date": f"{when} 10:00:00", "email": f"c{order_id}@x.com",
            "gender": "female", "total_value": f"{total}.00", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
            "sku": "pack_favorito", "product": "Pack Favorito", "utm_source": source,
            "utm_campaign": campaign, "utm_content": content,
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


LAST_DAY = f"{LAST.start_time:%Y-%m}-10"
ORDERS = [
    # Last month: three link-in-bio orders (one with odd spacing and case), two others.
    order("1", LAST_DAY, "link_in_bio", campaign="(organico)"),
    order("2", LAST_DAY, " Link_In_Bio ", total=150000, purchase_number=2, campaign="Retargeting"),
    order("3", LAST_DAY, "link_in_bio", source="instagram", total=50000),
    order("4", LAST_DAY, "LuisaMalOlor", source="facebook"),
    order("5", LAST_DAY, "undefined", source="google"),
    # Two months back: one link-in-bio order.
    order("6", f"{(LAST - 1).start_time:%Y-%m}-10", "link_in_bio"),
]

DAILY_ORDERS = [
    order("d1", "2026-09-01", "link_in_bio", total=100000),
    order("d2", "2026-09-01", "LuisaMalOlor", source="facebook", total=900000),  # not link in bio
    order("d3", "2026-09-02", "link_in_bio", total=70000),
    order("d4", "2026-09-02", "link_in_bio", source="instagram", total=30000),
]


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("data")
        write_orders_csv(ORDERS, "data/all_orders.csv")
        write_orders_csv(DAILY_ORDERS, DAILY_FILE)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)


class LinkInBioServiceTests(TempDirTestCase):
    def test_monthly_counts_link_in_bio_orders_only(self):
        rows, forecast = get_monthly_link_in_bio_trend(orders_csv_path="data/all_orders.csv", forecast_periods=2)
        by_month = {r["Month"]: r for r in rows}
        self.assertEqual((by_month[str(LAST)]["Link in Bio Total Value"], by_month[str(LAST)]["Orders"]),
                         ("300000", 3))
        self.assertEqual(by_month[str(LAST - 1)]["Orders"], 1)
        self.assertIn("Projected Link in Bio Sales", forecast[0])

    def test_daily_counts_link_in_bio_orders_only(self):
        rows = get_daily_sales_trend(orders_csv_path=DAILY_FILE, return_forecast=False, only="link_in_bio")
        self.assertEqual([(r["Date"], r["Total Orders"], r["Total Sales"]) for r in rows],
                         [("2026-09-01", 1, "100000"), ("2026-09-02", 2, "100000")])
        context = build_daily_sales_dashboard_context(DAILY_FILE, forecast_periods=3, only="link_in_bio")
        self.assertEqual(dict(zip(context["pie_labels"], context["pie_values"])),
                         {"ig": 170000.0, "instagram": 30000.0})

    def test_every_daily_text_has_spanish(self):
        missing = [v for t in SUBSET_TEXT.values() for v in t.values() if v not in SPANISH]
        self.assertEqual(missing, [])


class LinkInBioPageTests(TempDirTestCase):
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
            Option(meta_key="start_date_link_in_bio_daily_orders.csv", meta_value="01/09/2026"),
            Option(meta_key="end_date_link_in_bio_daily_orders.csv", meta_value="30/09/2026"),
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

    def get(self, url):
        with mock.patch("app.routes.monthly_by_kind.refresh_all_orders_if_needed"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_monthly_page(self):
        content = self.get("/link_in_bio_stats_monthly").split("<!-- Main Content Block -->", 1)[1]
        self.assertNotIn('<div class="alert alert-danger"', content)
        self.assertIn("Monthly Link in Bio Sales Trend &amp; Forecast", content)
        self.assertIn('id="linkInBioCountChart"', content)
        self.assertIn('id="linkInBioChart_ig"', content)
        self.assertNotIn('id="linkInBioChart_facebook"', content)
        self.assertNotIn('id="top-utm_content"', content)  # campaigns only on this page

    def test_monthly_top_campaigns(self):
        content = self.get("/link_in_bio_stats_monthly").split("<!-- Main Content Block -->", 1)[1]
        table = content.split('id="top-utm_campaign"', 1)[1].split("</table>", 1)[0]
        self.assertIn("Top 20 Campaigns for Link in Bio Sales", content)
        cells = [c.split("<", 1)[0].strip() for c in table.split('<td style="word-break: break-all;">')[1:]]
        # Retargeting 150,000 then (organico) 100,000; the facebook order isn't link in bio.
        self.assertEqual(cells, ["Retargeting", "(organico)"])
        # The other link-in-bio orders have no campaign: 150,000 of 400,000.
        self.assertIn("Link in bio orders without a campaign (undefined or N/A): 37.5% of link in bio sales", content)

    def test_daily_page(self):
        html = self.get("/link_in_bio_stats_daily")
        self.assertNotIn('<div class="alert alert-danger"', html)
        self.assertIn("Daily Link in Bio Sales Trend and Forecast", html)
        self.assertIn('id="dailySubsetChart"', html)
        self.assertNotIn('id="dailySalesAndRepurchasesChart"', html)
        counts = re.search(r'label: "Link in Bio Orders",\s*data: \[([^\]]*)\]', html).group(1)
        self.assertEqual([int(v) for v in counts.split(",") if v.strip()], [1, 2])
        self.assertIn('value="link_in_bio_daily_orders.csv"', html)

    def test_channels_menu_links_both_pages(self):
        html = self.get("/link_in_bio_stats_daily")
        channels = html.split('<div class="nav-group-label">Channels</div>', 1)[1].split('<div class="nav-group-label">', 1)[0]
        self.assertIn('href="/link_in_bio_stats_daily" class="active"', channels)
        self.assertIn('href="/link_in_bio_stats_monthly" class=""', channels)


if __name__ == "__main__":
    unittest.main()
