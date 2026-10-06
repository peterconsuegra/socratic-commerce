"""
Orders from the store: gender, purchase_number and is_repurchase are copied as
the API sends them, every repurchase figure reads them instead of matching
customers, and the sync keeps the copy current.

No request leaves the process: the orders API is replaced by a recorder.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime
from unittest import mock

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import Option  # noqa: E402
from app.routes import common  # noqa: E402
from app.services import get_data  # noqa: E402
from app.services.secrets import set_secret  # noqa: E402
from app.services.get_data import (  # noqa: E402
    OutdatedOrdersFile,
    fetch_orders,
    load_orders,
    orders_file_is_current,
    write_orders_csv,
)
from app.services.rankings import get_top_10_months_by_sales  # noqa: E402
from app.services.recurrent_customers import customer_orders, get_recurrent_customers  # noqa: E402
from app.services.stats import _compute_orders_stats  # noqa: E402


def api_order(order_id, order_date, email, purchase_number, gender="female", total="100000.00", **extra):
    """One order shaped like the API's JSON."""
    item = {
        "order_id": order_id, "order_date": order_date, "order_date_utc": "N/A",
        "name": "ana maría", "last_name": "N/A", "gender": gender, "email": email,
        "phone": "3104792445", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
        "total_value": total, "product": "Pack", "sku": "pack_favorito",
        "utm_source": "Facebook", "purchase_number": purchase_number,
        "is_repurchase": purchase_number if purchase_number == "N/A" else purchase_number > 1,
    }
    item.update(extra)
    return item


class FakeOrdersApi:
    """Stands in for requests.get: records each call's params and answers
    with the orders whose date falls in the requested window."""

    def __init__(self, orders=(), status=200):
        self.orders = list(orders)
        self.status = status
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        start = datetime.strptime(params["start_date"], "%d/%m/%Y").date()
        end = datetime.strptime(params["end_date"], "%d/%m/%Y").date()
        chunk = [o for o in self.orders if start <= date.fromisoformat(o["order_date"][:10]) <= end]
        return mock.Mock(status_code=self.status, json=mock.Mock(return_value=chunk),
                         raise_for_status=mock.Mock(side_effect=None if self.status == 200 else
                                                    get_data.requests.exceptions.HTTPError(str(self.status))))


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, orders, name="orders.csv"):
        path = os.path.join(self.tmp, name)
        write_orders_csv(orders, path)
        return path


class RowTests(TempDirTestCase):
    def test_store_fields_are_copied_as_sent(self):
        path = self.write([
            api_order("1", "2026-09-02 10:00:00", "a@x.com", 3, gender="male"),
            api_order("2", "2026-09-01 10:00:00", "b@x.com", 1, gender="unknown"),
        ])
        data = load_orders(path).set_index("order_id")
        self.assertEqual(data.loc["1", "gender"], "male")
        self.assertEqual(data.loc["2", "gender"], "unknown")
        self.assertEqual((data.loc["1", "purchase_number"], data.loc["1", "is_repurchase"]), (3, True))
        self.assertEqual((data.loc["2", "purchase_number"], data.loc["2", "is_repurchase"]), (1, False))

    def test_unnumbered_order_is_neither_a_repurchase_nor_a_first_purchase(self):
        data = load_orders(self.write([api_order("1", "2026-09-02 10:00:00", "a@x.com", "N/A")]))
        self.assertTrue(data["purchase_number"].isna().all())
        self.assertFalse(data["is_repurchase"].any())
        self.assertFalse(data["purchase_number"].eq(1).any())

    def test_a_gender_the_store_did_not_send_is_unknown_never_guessed(self):
        data = load_orders(self.write([api_order("1", "2026-09-02 10:00:00", "a@x.com", 1, gender="N/A")]))
        self.assertEqual(data["gender"].tolist(), ["unknown"])

    def test_file_written_before_the_store_fields_is_refused(self):
        path = os.path.join(self.tmp, "old.csv")
        with open(path, "w") as f:
            f.write("order_id,order_date,email,total_value,gender\n1,2026-09-02 10:00:00,a@x.com,1,female\n")
        self.assertFalse(orders_file_is_current(path))
        with self.assertRaises(OutdatedOrdersFile):
            load_orders(path)


class FetchTests(unittest.TestCase):
    def test_full_history_is_fetched_one_calendar_year_at_a_time_newest_first(self):
        api = FakeOrdersApi([api_order("2", "2026-01-05 10:00:00", "a@x.com", 2),
                             api_order("1", "2021-11-17 14:07:03", "a@x.com", 1)])
        with mock.patch.object(get_data.requests, "get", api):
            orders = fetch_orders(orders_url="https://store.test/orders", api_key="test")
        today = datetime.now(get_data.BOGOTA).date()
        years = list(range(today.year, get_data.FIRST_ORDER_YEAR - 1, -1))
        self.assertEqual([c["start_date"] for c in api.calls], [f"01/01/{y}" for y in years])
        self.assertEqual(api.calls[0]["end_date"], today.strftime("%d/%m/%Y"))
        self.assertEqual([c["end_date"] for c in api.calls[1:]], [f"31/12/{y}" for y in years[1:]])
        self.assertEqual([o["order_id"] for o in orders], ["2", "1"])

    def test_a_window_inside_one_year_is_one_request(self):
        api = FakeOrdersApi()
        with mock.patch.object(get_data.requests, "get", api):
            fetch_orders(orders_url="https://store.test/orders", api_key="test",
                         start_date="01/09/2026", end_date="30/09/2026")
        self.assertEqual(api.calls, [{"start_date": "01/09/2026", "end_date": "30/09/2026"}])

    def test_a_failed_year_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.object(get_data.requests, "get", FakeOrdersApi(status=429)):
                ok, payload = get_data.fetch_orders_and_write_csv(
                    orders_url="https://store.test/orders", api_key="test", file_name="all_orders.csv", cwd=tmp)
            self.assertFalse(ok)
            self.assertFalse(os.path.exists(os.path.join(tmp, "data", "all_orders.csv")))
        finally:
            shutil.rmtree(tmp)


class RepurchaseFigureTests(TempDirTestCase):
    """The store links customers by phone; the app must not second-guess it by email."""

    def setUp(self):
        super().setUp()
        self.path = self.write([
            # One customer, second purchase under a new email: a repurchase.
            api_order("1", "2026-08-01 10:00:00", "ana@x.com", 1),
            api_order("2", "2026-09-01 10:00:00", "ana.new@x.com", 2, total="50000.00"),
            # Two customers typed with the store's own email: two first purchases.
            api_order("3", "2026-09-02 10:00:00", "ventas@store.test", 1),
            api_order("4", "2026-09-03 10:00:00", "ventas@store.test", 1),
        ])

    def test_top_months_count_the_store_flag(self):
        months = {m["Month"]: m for m in get_top_10_months_by_sales(self.path)}
        self.assertEqual(months["September 2026"]["Repurchases"], 1)
        self.assertEqual(months["September 2026"]["Repurchase Total Value"], "$50.000")
        self.assertEqual(months["August 2026"]["Repurchases"], 0)

    def test_stats_count_customers_from_purchase_numbers(self):
        data = load_orders(self.path)
        stats = _compute_orders_stats(data, customer_orders(data))
        self.assertEqual((stats["customers"], stats["repeat_customers"], stats["repeat_orders"]), (3, 1, 1))

    def test_customer_lists_show_the_store_purchase_count(self):
        result = get_recurrent_customers(orders_csv_path=self.path, min_orders=1)
        by_email = {r["email"]: r for r in result["rows"]}
        # Under its new email Ana has one order, but it is her second purchase.
        self.assertEqual(by_email["ana.new@x.com"]["orders_count"], 2)
        self.assertEqual(by_email["ana.new@x.com"]["total_spent"], 50000.0)
        # The shared store email holds two customers' first purchases.
        self.assertEqual(by_email["ventas@store.test"]["orders_count"], 1)

        repeat = get_recurrent_customers(orders_csv_path=self.path, min_orders=2)
        self.assertEqual([r["email"] for r in repeat["rows"]], ["ana.new@x.com"])
        self.assertEqual(repeat["summary"]["recurrent_orders"], 1)


class RefreshTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        # The same layout as the app: order files live in <project root>/data.
        self.root, self.tmp = self.tmp, os.path.join(self.tmp, "data")
        os.makedirs(self.tmp)
        self.app = create_app()
        self.app.config.update(TESTING=True, DATA_DIR=self.tmp, PROJECT_ROOT=self.root,
                               ALL_ORDERS_CSV=os.path.join(self.tmp, "all_orders.csv"),
                               ALL_ORDERS_CACHE_FILE=os.path.join(self.tmp, ".all_orders_cache_ts"))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp = self.root
        super().tearDown()

    def cache(self, fetched_at):
        self.write([api_order("1", "2026-09-02 10:00:00", "a@x.com", 1)], "all_orders.csv")
        with open(self.app.config["ALL_ORDERS_CACHE_FILE"], "w") as f:
            f.write(str(fetched_at))

    def test_a_copy_from_before_the_store_fields_is_refreshed(self):
        self.cache(time.time())
        with open(self.app.config["ALL_ORDERS_CSV"], "w") as f:
            f.write("order_id,order_date,email,total_value\n1,2026-09-02 10:00:00,a@x.com,1\n")
        self.assertTrue(common.should_refresh_all_orders())

    def test_a_copy_from_before_the_nightly_rebuild_is_refreshed(self):
        self.cache(common._last_nightly_rebuild() - 60)
        self.assertTrue(common.should_refresh_all_orders())
        self.cache(common._last_nightly_rebuild() + 60)
        self.assertFalse(common.should_refresh_all_orders(max_age_seconds=10 ** 9))

    def test_saved_date_range_files_are_resynced_over_their_saved_dates(self):
        old = os.path.join(self.tmp, "daily_sales_orders.csv")
        with open(old, "w") as f:
            f.write("order_id,order_date,email,total_value,gender\n1,2026-09-02 10:00:00,a@x.com,1,female\n")
        set_secret("api_key", "test")
        db.session.add_all([Option(meta_key=k, meta_value=v) for k, v in {
            "orders_url": "https://store.test/orders",
            "start_date_daily_sales_orders.csv": "01/09/2026", "end_date_daily_sales_orders.csv": "30/09/2026",
            # Saved, but its file was never fetched: nothing to re-sync.
            "start_date_google_sales_orders.csv": "01/08/2026", "end_date_google_sales_orders.csv": "31/08/2026",
        }.items()])
        db.session.commit()

        api = FakeOrdersApi([api_order("1", "2026-09-02 10:00:00", "a@x.com", 1)])
        with mock.patch.object(get_data.requests, "get", api):
            synced = common.resync_range_files()

        self.assertEqual(synced, ["daily_sales_orders.csv"])
        self.assertEqual(api.calls, [{"start_date": "01/09/2026", "end_date": "30/09/2026"}])
        self.assertTrue(orders_file_is_current(old))


if __name__ == "__main__":
    unittest.main()
