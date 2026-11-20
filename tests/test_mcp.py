"""
/mcp, the MCP server for agents: the periods it lists, sales by period (with
day/week/month rows), comparing two ranges, and the JSON-RPC plumbing and
token auth around them.

In-memory SQLite and a temporary orders file; nothing reaches the store.

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
from app.models import ApiToken  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402
from app.services.periods import PeriodError  # noqa: E402
from app.services.sales_periods import compare_periods, describe_periods, sales_by_period  # noqa: E402

NOW = pd.Timestamp("2026-03-10 15:00")


def order(order_id, when, total, purchase_number=1):
    return {"order_id": order_id, "order_date": when, "email": f"c{order_id}@x.com",
            "total_value": f"{total}.00", "city": "BOGOTA", "gender": "female",
            "utm_source": "facebook", "utm_campaign": "c", "utm_content": "a",
            "purchase_number": purchase_number if purchase_number else "N/A",
            "is_repurchase": bool(purchase_number and purchase_number > 1)}


ORDERS = [
    order("1", "2026-01-31 23:59:59", 10000),             # January
    order("2", "2026-02-01 00:00:00", 20000),             # February, a Sunday
    order("3", "2026-02-02 09:00:00", 30000, 2),          # Monday
    order("4", "2026-02-28 12:00:00", 40000, 3),
    order("5", "2026-02-15 12:00:00", 50000, None),       # not numbered yet
    order("6", "2026-03-10 08:00:00", 60000),             # today, before NOW
    order("7", "2026-03-10 18:00:00", 70000),             # today, after NOW
]


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class SalesPeriodsServiceTests(ServiceTestCase):
    def test_list_periods_gives_todays_dates_and_the_history(self):
        info = describe_periods(self.path, now=NOW)
        self.assertEqual(info["today"], "2026-03-10")
        self.assertEqual(info["presets"][1], {"period": "last_7d", "label": "Last 7 days",
                                              "start_date": "2026-03-03", "end_date": "2026-03-10", "days": 8})
        self.assertEqual(info["custom"]["max_days"], 366)
        self.assertEqual(info["order_history"]["first_order_date"], "2026-01-31")
        self.assertEqual(info["currency"], "COP")

    def test_custom_month_totals(self):
        out = sales_by_period(self.path, "custom", "2026-02-01", "2026-02-28", now=NOW)
        totals = out["periods"]["custom"]["totals"]
        self.assertEqual(totals["total_sales"], 140000.0)
        self.assertEqual(totals["total_orders"], 4)
        self.assertEqual(totals["average_order_value"], 35000.0)
        self.assertEqual((totals["first_time_orders"], totals["first_time_sales"]), (1, 20000.0))
        self.assertEqual((totals["repurchase_orders"], totals["repurchase_sales"]), (2, 70000.0))
        self.assertEqual(totals["repurchase_sales_percentage"], 50.0)
        self.assertEqual(totals["unnumbered_orders"], 1)

    def test_presets_stop_at_now(self):
        today = sales_by_period(self.path, "today", now=NOW)["periods"]["today"]["totals"]
        self.assertEqual((today["total_orders"], today["total_sales"]), (1, 60000.0))

    def test_week_rows_start_monday_and_are_clipped_to_the_range(self):
        rows = sales_by_period(self.path, "custom", "2026-02-01", "2026-02-10",
                               breakdown="week", now=NOW)["periods"]["custom"]["rows"]
        self.assertEqual([(r["start_date"], r["end_date"], r["total_sales"]) for r in rows], [
            ("2026-02-01", "2026-02-01", 20000.0),
            ("2026-02-02", "2026-02-08", 30000.0),
            ("2026-02-09", "2026-02-10", 0.0),
        ])

    def test_month_rows_include_every_month(self):
        rows = sales_by_period(self.path, "custom", "2026-01-15", "2026-03-10",
                               breakdown="month", now=NOW)["periods"]["custom"]["rows"]
        self.assertEqual([(r["start_date"], r["end_date"], r["total_orders"]) for r in rows], [
            ("2026-01-15", "2026-01-31", 1), ("2026-02-01", "2026-02-28", 4), ("2026-03-01", "2026-03-10", 1)])

    def test_day_rows_cover_every_day(self):
        rows = sales_by_period(self.path, "last_7d", breakdown="day", now=NOW)["periods"]["last_7d"]["rows"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1]["total_sales"], 60000.0)

    def test_bad_breakdown(self):
        with self.assertRaises(PeriodError):
            sales_by_period(self.path, "all", breakdown="year", now=NOW)

    def test_compare_with_the_days_right_before(self):
        out = compare_periods(self.path, "2026-02-01", "2026-02-28", now=NOW)
        self.assertEqual((out["previous"]["start_date"], out["previous"]["end_date"]), ("2026-01-04", "2026-01-31"))
        self.assertEqual(out["previous"]["totals"]["total_sales"], 10000.0)
        self.assertEqual(out["change"]["total_sales"], {"difference": 130000.0, "percentage": 1300.0})
        self.assertIsNone(out["change"]["repurchase_sales"]["percentage"])

    def test_compare_with_a_given_range(self):
        out = compare_periods(self.path, "2026-03-01", "2026-03-10", "2026-02-01", "2026-02-10", now=NOW)
        self.assertEqual(out["previous"]["totals"]["total_sales"], 50000.0)
        self.assertEqual(out["current"]["totals"]["total_sales"], 60000.0)  # the 18:00 order is after NOW
        self.assertEqual(out["change"]["total_sales"]["percentage"], 20.0)

    def test_compare_needs_both_compare_days(self):
        with self.assertRaises(PeriodError):
            compare_periods(self.path, "2026-02-01", "2026-02-28", "2026-01-01", None, now=NOW)


class McpEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "all_orders.csv")
        today = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        write_orders_csv([order("1", f"{today - pd.Timedelta(days=2):%Y-%m-%d} 10:00:00", 25000)], path)
        self.app = create_app()
        self.app.config.update(TESTING=True, ALL_ORDERS_CSV=path)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        token, self.raw = ApiToken.generate("agent")
        db.session.add(token)
        db.session.commit()
        self.client = self.app.test_client()
        patcher = mock.patch("app.routes.mcp.refresh_all_orders_if_needed")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rpc(self, method, params=None, msg_id=1, token=True):
        body = {"jsonrpc": "2.0", "method": method}
        if msg_id is not None:
            body["id"] = msg_id
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {self.raw}"} if token else {}
        return self.client.post("/mcp", json=body, headers=headers)

    def test_needs_a_token(self):
        self.assertEqual(self.rpc("tools/list", token=False).status_code, 401)

    def test_initialize_negotiates_the_version(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}}).get_json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "second-order")
        odd = self.rpc("initialize", {"protocolVersion": "1999-01-01"}).get_json()["result"]
        self.assertEqual(odd["protocolVersion"], "2025-06-18")

    def test_notifications_get_202(self):
        self.assertEqual(self.rpc("notifications/initialized", msg_id=None).status_code, 202)

    def test_tools_list(self):
        names = [t["name"] for t in self.rpc("tools/list").get_json()["result"]["tools"]]
        self.assertEqual(names, ["list_periods", "get_sales_by_period", "compare_periods"])

    def test_sales_call(self):
        result = self.rpc("tools/call", {"name": "get_sales_by_period",
                                         "arguments": {"period": "last_7d"}}).get_json()["result"]
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["periods"]["last_7d"]["totals"]["total_sales"], 25000.0)
        self.assertIn('"total_sales": 25000.0', result["content"][0]["text"])

    def test_bad_arguments_are_a_tool_error(self):
        result = self.rpc("tools/call", {"name": "get_sales_by_period",
                                         "arguments": {"period": "custom", "since": "2026-01-01"}}).get_json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("needs both since and until", result["content"][0]["text"])
        extra = self.rpc("tools/call", {"name": "list_periods", "arguments": {"x": 1}}).get_json()["result"]
        self.assertTrue(extra["isError"])

    def test_unknown_tool_and_method(self):
        self.assertEqual(self.rpc("tools/call", {"name": "nope"}).get_json()["error"]["code"], -32602)
        self.assertEqual(self.rpc("resources/list").get_json()["error"]["code"], -32601)

    def test_get_is_405(self):
        self.assertEqual(self.client.get("/mcp").status_code, 405)


if __name__ == "__main__":
    unittest.main()
