"""
A custom date range that runs through today counts today's orders so far;
the presets, and a custom range ending earlier, stop at the end of yesterday
as before (app/services/periods.py: range_includes_today, orders_cutoff).

Runs in a temporary working directory; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest

import pandas as pd

from app.services.checkout_insights import get_checkout_insights_daily
from app.services.daily_repurchases import get_daily_repurchases_trend
from app.services.daily_sales import get_daily_sales_trend
from app.services.facebook_insights import get_facebook_insights_daily
from app.services.get_data import write_orders_csv
from app.services.periods import now_bogota_naive, orders_cutoff, range_includes_today

TODAY = now_bogota_naive().normalize()
YESTERDAY = TODAY - pd.Timedelta(days=1)


def ddmmyyyy(day: pd.Timestamp) -> str:
    return f"{day:%d/%m/%Y}"


def order(order_id, day, total, purchase_number=1):
    """One order shaped like the API's JSON, placed at the very start of day."""
    return {
        "order_id": order_id, "order_date": f"{day:%Y-%m-%d} 00:00:00", "name": "Ana", "gender": "female",
        "email": f"c{order_id}@x.com", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
        "total_value": f"{total}.00", "sku": "pack_favorito", "utm_source": "facebook",
        "utm_campaign": "tofu", "utm_answer": "instagram", "purchase_number": purchase_number,
        "is_repurchase": purchase_number > 1,
    }


ORDERS = [order("1", YESTERDAY, 100000, 2), order("2", TODAY, 40000, 2)]


class HelperTests(unittest.TestCase):
    def test_only_an_end_date_of_today_or_later_includes_today(self):
        self.assertTrue(range_includes_today(ddmmyyyy(TODAY)))
        self.assertFalse(range_includes_today(ddmmyyyy(YESTERDAY)))
        self.assertFalse(range_includes_today(None))
        self.assertFalse(range_includes_today("2026-10-23"))

    def test_cutoff_is_end_of_yesterday_unless_today_counts(self):
        now = pd.Timestamp("2026-10-23 15:30")
        self.assertEqual(orders_cutoff(False, now), pd.Timestamp("2026-10-22 23:59:59.999999"))
        self.assertEqual(orders_cutoff(True, now), now)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = os.path.join(self.tmp, "orders.csv")
        write_orders_csv(ORDERS, self.csv)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def days(self, rows):
        return [row["Date"] for row in rows]

    def test_daily_sales_trend(self):
        self.assertEqual(self.days(get_daily_sales_trend(return_forecast=False, orders_csv_path=self.csv)),
                         [f"{YESTERDAY:%Y-%m-%d}"])
        self.assertEqual(self.days(get_daily_sales_trend(return_forecast=False, orders_csv_path=self.csv,
                                                         include_today=True)),
                         [f"{YESTERDAY:%Y-%m-%d}", f"{TODAY:%Y-%m-%d}"])

    def test_daily_repurchases_follow_the_ranges_end_date(self):
        def total(end):
            rows = get_daily_repurchases_trend(return_forecast=False, orders_csv_path=self.csv,
                                               start_date=ddmmyyyy(YESTERDAY), end_date=ddmmyyyy(end))
            return len(rows)
        self.assertEqual(total(YESTERDAY), 1)
        self.assertEqual(total(TODAY), 2)

    def test_checkout_insights_follow_the_ranges_end_date(self):
        def days(end):
            result = get_checkout_insights_daily(orders_csv_path=self.csv, start_date=ddmmyyyy(YESTERDAY),
                                                 end_date=ddmmyyyy(end))
            return len(result["total_daily_trend"])
        self.assertEqual(days(YESTERDAY), 1)
        self.assertEqual(days(TODAY), 2)

    def test_facebook_insights(self):
        def sales(**kwargs):
            return sum(result for result in get_facebook_insights_daily(orders_csv_path=self.csv, **kwargs)["pie_values"])
        self.assertEqual(sales(), 100000)
        self.assertEqual(sales(include_today=True), 140000)


if __name__ == "__main__":
    unittest.main()
