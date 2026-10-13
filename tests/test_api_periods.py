"""
The JSON summaries' windows - the trailing presets and a custom range of Bogotá
days (period=custom&since&until) - the 400s for requests no window answers, and
what keeps the API quick: the parsed orders cached per file version, and one
sync of the orders at a time across the web workers.

Orders are written relative to today where a preset must reach them. In-memory
SQLite; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

import pandas as pd

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import User  # noqa: E402
from app.routes import common  # noqa: E402
from app.services import get_data  # noqa: E402
from app.services.get_data import BOGOTA, load_orders, write_orders_csv  # noqa: E402
from app.services.periods import PeriodError, resolve_windows  # noqa: E402

NOW = pd.Timestamp("2026-10-13 10:30")  # a fixed "now" for the window rules
TODAY = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).normalize()


def order(order_id, when, source="facebook", campaign="Prospecting", content="ad_a", repurchase=False,
          total=100000):
    """One order shaped like the API's JSON."""
    return {"order_id": order_id, "order_date": f"{when:%Y-%m-%d %H:%M:%S}", "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "city": "BOGOTA (C/MARCA)", "gender": "female",
            "utm_source": source, "utm_campaign": campaign, "utm_content": content,
            "purchase_number": 2 if repurchase else 1, "is_repurchase": repurchase}


def days_ago(days, at="10:00:00"):
    return pd.Timestamp(f"{TODAY - pd.Timedelta(days=days):%Y-%m-%d} {at}")


# Around the last 30 days, with orders on the first moment of a preset and the
# last moment before it.
RECENT = [
    order("1", days_ago(1, "23:59:59")),
    order("2", days_ago(3), "wati", "wati", "N/A", repurchase=True),
    order("3", days_ago(7, "00:00:00"), content="ad_b"),
    order("4", days_ago(8, "23:59:59")),
    order("5", days_ago(20), "google", "search", "kw", repurchase=True, total=50000),
    order("6", days_ago(30, "00:00:00"), campaign="Retargeting"),
    order("7", days_ago(31, "23:59:59")),
    order("8", days_ago(40), repurchase=True),
]


class WindowRuleTests(unittest.TestCase):
    def test_presets_run_from_n_days_back_through_today(self):
        windows = resolve_windows("all", now=NOW)
        self.assertEqual([w.key for w in windows], ["today", "last_7d", "last_30d", "last_90d", "last_180d"])
        self.assertEqual(windows[1].describe(),
                         {"label": "Last 7 days", "start_date": "2026-10-06", "end_date": "2026-10-13"})

    def test_a_custom_period_echoes_its_days(self):
        [window] = resolve_windows("custom", "2026-01-01", "2026-01-31", now=NOW)
        self.assertEqual(window.key, "custom")
        self.assertEqual(window.describe(),
                         {"label": "2026-01-01 – 2026-01-31", "start_date": "2026-01-01", "end_date": "2026-01-31"})

    def test_a_custom_period_takes_whole_days_both_ends_included(self):
        data = pd.DataFrame({"order_date": pd.to_datetime([
            "2025-12-31 23:59:59", "2026-01-01 00:00:00", "2026-01-31 23:59:59", "2026-02-01 00:00:00"])})
        [window] = resolve_windows("custom", "2026-01-01", "2026-01-31", now=NOW)
        self.assertEqual(list(window.orders(data, NOW).index), [1, 2])

    def test_366_days_both_ends_counted_is_the_most(self):
        self.assertEqual(len(resolve_windows("custom", "2025-01-01", "2026-01-01", now=NOW)), 1)

    def test_requests_no_window_answers(self):
        cases = {
            ("custom", None, None): "period=custom needs both since and until, as YYYY-MM-DD.",
            ("custom", "2026-01-01", ""): "period=custom needs both since and until, as YYYY-MM-DD.",
            ("custom", "2026-1-01", "2026-01-31"): "since must be a date as YYYY-MM-DD, got '2026-1-01'.",
            ("custom", "2026-02-30", "2026-03-01"): "since '2026-02-30' is not a real date.",
            ("custom", "2026-02-01", "2026-01-31"): "since (2026-02-01) is after until (2026-01-31).",
            ("custom", "2026-10-01", "2026-10-14"): "until (2026-10-14) is after today in Bogotá (2026-10-13).",
            ("custom", "2025-01-01", "2026-01-02"): "The period spans 367 days; the most is 366, both ends counted.",
            ("last_30d", "2026-01-01", "2026-01-31"): "since and until go with period=custom only, not period=last_30d.",
            (None, "2026-01-01", None): "since and until go with period=custom only, not period=all.",
            ("yesterday", None, None): "Invalid period 'yesterday'. Valid values: "
                                       "['all', 'today', 'last_7d', 'last_30d', 'last_90d', 'last_180d', 'custom']",
        }
        for (period, since, until), message in cases.items():
            with self.subTest(period=period, since=since, until=until):
                with self.assertRaises(PeriodError) as caught:
                    resolve_windows(period, since, until, now=NOW)
                self.assertEqual(str(caught.exception), message)


class SummaryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(RECENT, path)
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

    def get(self, url, status=200, client=None):
        with mock.patch("app.routes.api.refresh_all_orders_if_needed"):
            response = (client or self.client).get(url)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.get_json()

    def test_every_summary_answers_a_custom_period_under_custom(self):
        since, until = f"{days_ago(30):%Y-%m-%d}", f"{TODAY:%Y-%m-%d}"
        for url in ["/api/utm_source_summary?", "/api/utm_campaign_summary?",
                    "/api/utm_content_summary?campaign_name=Prospecting&", "/api/wati_summary?"]:
            with self.subTest(url=url):
                body = self.get(f"{url}period=custom&since={since}&until={until}")
                self.assertEqual(body["timezone"], "America/Bogota")
                self.assertIn("orders_as_of", body)
                self.assertEqual(list(body["periods"]), ["custom"])
                self.assertEqual({k: body["periods"]["custom"][k] for k in ("label", "start_date", "end_date")},
                                 {"label": f"{since} – {until}", "start_date": since, "end_date": until})

    def test_a_preset_and_its_own_days_answer_alike(self):
        for url in ["/api/utm_source_summary?", "/api/utm_campaign_summary?limit=0&",
                    "/api/utm_content_summary?campaign_name=Prospecting&"]:
            for preset in ["last_7d", "last_30d"]:
                with self.subTest(url=url, preset=preset):
                    by_preset = self.get(f"{url}period={preset}")["periods"][preset]
                    by_days = self.get(f"{url}period=custom&since={by_preset['start_date']}"
                                       f"&until={by_preset['end_date']}")["periods"]["custom"]
                    self.assertGreater(by_preset["totals"]["total_orders"], 0)
                    by_preset.pop("label"), by_days.pop("label")
                    self.assertEqual(by_days, by_preset)

    def test_the_window_edges(self):
        totals = self.get("/api/utm_source_summary?period=last_7d")["periods"]["last_7d"]["totals"]
        self.assertEqual(totals["total_orders"], 3)  # yesterday, 3 days ago, and 7 days ago at 00:00
        self.assertEqual(totals["repurchase_orders"], 1)

    def test_a_request_no_window_answers_is_a_400(self):
        body = self.get("/api/utm_campaign_summary?period=custom&since=2026-01-01", status=400)
        self.assertEqual(body, {"status": "error",
                                "message": "period=custom needs both since and until, as YYYY-MM-DD."})
        body = self.get("/api/utm_source_summary?since=2026-01-01&until=2026-01-31", status=400)
        self.assertEqual(body["message"], "since and until go with period=custom only, not period=all.")
        body = self.get("/api/utm_content_summary?campaign_name=Prospecting&period=custom"
                        "&since=2026-02-30&until=2026-03-01", status=400)
        self.assertEqual(body["message"], "since '2026-02-30' is not a real date.")

    def test_a_request_without_a_token_is_refused_first(self):
        body = self.get("/api/utm_source_summary?period=custom&since=bad", status=401,
                        client=self.app.test_client())
        self.assertIn("Unauthorized", body["message"])

    def test_an_unknown_campaign_is_still_a_404(self):
        since = f"{days_ago(7):%Y-%m-%d}"
        body = self.get(f"/api/utm_content_summary?campaign_name=Nope&period=custom&since={since}"
                        f"&until={TODAY:%Y-%m-%d}", status=404)
        self.assertEqual(body["message"], "No campaign matching 'Nope' was found.")

    def test_presets_say_their_timezone_too(self):
        body = self.get("/api/utm_source_summary?period=last_30d")
        self.assertEqual((body["timezone"], list(body["periods"])), ("America/Bogota", ["last_30d"]))


class LoadOrdersCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(RECENT[:2], self.path)
        get_data._PARSED.clear()

    def tearDown(self):
        get_data._PARSED.clear()
        shutil.rmtree(self.tmp)

    def test_a_file_is_parsed_once_per_version(self):
        with mock.patch.object(get_data.pd, "read_csv", wraps=pd.read_csv) as read_csv:
            first, second = load_orders(self.path), load_orders(self.path)
        self.assertEqual(read_csv.call_count, 1)
        pd.testing.assert_frame_equal(first, second)

    def test_each_caller_edits_its_own_copy(self):
        mine = load_orders(self.path)
        mine["utm_source"] = "changed"
        mine.loc[mine.index[0], "city"] = "changed"
        mine["extra"] = 1
        fresh = load_orders(self.path)
        self.assertNotIn("changed", set(fresh["utm_source"]) | set(fresh["city"]))
        self.assertNotIn("extra", fresh.columns)

    def test_a_rewritten_file_is_parsed_again(self):
        self.assertEqual(len(load_orders(self.path)), 2)
        write_orders_csv(RECENT, self.path)
        self.assertEqual(len(load_orders(self.path)), len(RECENT))


class OrdersSyncTests(unittest.TestCase):
    """One sync of all_orders.csv at a time across the web workers."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.data = os.path.join(self.root, "data")
        os.makedirs(self.data)
        self.app = create_app()
        self.app.config.update(TESTING=True, DATA_DIR=self.data, PROJECT_ROOT=self.root,
                               ALL_ORDERS_CSV=os.path.join(self.data, "all_orders.csv"),
                               ALL_ORDERS_CACHE_FILE=os.path.join(self.data, ".all_orders_cache_ts"))
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()
        shutil.rmtree(self.root)

    def synced(self, *args, **kwargs):
        """What a finished sync leaves: the orders file and its timestamp."""
        write_orders_csv(RECENT[:1], self.app.config["ALL_ORDERS_CSV"])
        with open(self.app.config["ALL_ORDERS_CACHE_FILE"], "w") as f:
            f.write(str(time.time()))

    def test_a_second_holder_is_turned_away(self):
        with common._sync_lock(wait=False) as first, common._sync_lock(wait=False) as second:
            self.assertEqual((first, second), (True, False))
        with common._sync_lock(wait=False) as again:
            self.assertTrue(again)

    def test_a_first_sync_uses_the_one_another_worker_finished(self):
        @contextmanager
        def waited_for_another_worker(wait):
            self.synced()  # the other worker's sync ended while this one waited
            yield True

        with mock.patch.object(common, "_sync_lock", waited_for_another_worker), \
                mock.patch.object(common, "generate_all_orders_csv") as generate:
            common.refresh_all_orders_if_needed()
        generate.assert_not_called()

    def test_a_first_sync_with_none_running_fetches(self):
        with mock.patch.object(common, "generate_all_orders_csv", side_effect=self.synced) as generate:
            common.refresh_all_orders_if_needed()
        generate.assert_called_once()

    def test_a_worker_start_syncs_missing_or_stale_orders_in_the_background(self):
        with mock.patch.object(common, "_refresh_in_background") as background:
            common.start_orders_sync_if_needed(self.app)
            background.assert_called_once_with(self.app, common.API_ORDERS_MAX_AGE_SECONDS)
            self.synced()
            common.start_orders_sync_if_needed(self.app)
        background.assert_called_once()

    def test_a_background_refresh_leaves_a_running_sync_alone(self):
        with mock.patch.object(common, "generate_all_orders_csv") as generate, \
                common._sync_lock(wait=False) as holding:
            self.assertTrue(holding)
            common._refresh_in_background(self.app, 60)
            for thread in threading.enumerate():
                if thread.name == "all-orders-refresh":
                    thread.join(5)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
