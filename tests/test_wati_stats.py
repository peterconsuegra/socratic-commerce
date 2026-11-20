"""
/wati_stats: utm_campaign=wati orders, last 30 days vs last 7 days.

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

import pandas as pd  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import User  # noqa: E402
from app.routes.wati_stats import build_wati_stats, conversion_rates  # noqa: E402
from app.services import roxy_leads  # noqa: E402
from app.services.roxy_leads import RoxyLeadsError, fetch_new_leads  # noqa: E402
from app.services.secrets import get_secret, set_secret  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402
from app.services.periods import now_bogota_naive  # noqa: E402


def days_ago(n):
    return (now_bogota_naive().normalize() - pd.Timedelta(days=n)).strftime("%Y-%m-%d")


def order(order_id, campaign, day, total="100000.00", purchase_number=1, gender="female"):
    return {"order_id": order_id, "order_date": f"{day} 10:00:00", "order_date_utc": f"{day}T15:00:00Z",
            "name": f"c{order_id}", "email": f"c{order_id}@x.com", "phone": f"300{order_id:0>7}",
            "total_value": total, "city": "BOGOTA (C/MARCA)", "sku": "pack_favorito", "product": "Pack Favorito",
            "gender": gender, "utm_campaign": campaign,
            "purchase_number": purchase_number, "is_repurchase": purchase_number > 1}


ORDERS = [
    # Last 7 days: 2 wati orders, 200,000 of sales, one a repurchase.
    order("1", "wati", days_ago(2)),
    order("2", " WATI ", days_ago(3), purchase_number=2, gender="male"),
    # Only in the 30-day window.
    order("3", "wati", days_ago(15), total="300000.00"),
    order("4", "wati", days_ago(20), total="200000.00"),
    # Out of both windows, or not wati.
    order("5", "wati", days_ago(45)),
    order("6", "CreativeTest_01", days_ago(1), total="999000.00"),
]


class WatiStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "all_orders.csv")
        write_orders_csv(ORDERS, self.path)
        self.app = create_app()
        self.app.config.update(TESTING=True, ALL_ORDERS_CSV=self.path)
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

    def test_windows_count_only_wati_orders(self):
        stats = build_wati_stats(self.path)
        self.assertEqual(stats["long"]["total_orders"], 4)
        self.assertEqual(stats["long"]["total_sales"], 700000.0)
        self.assertEqual(stats["short"]["total_orders"], 2)
        self.assertEqual(stats["short"]["total_sales"], 200000.0)
        self.assertEqual(stats["short"]["repurchase_orders"], 1)
        self.assertEqual(stats["short"]["first_time_orders"], 1)

    def test_per_day_change(self):
        stats = build_wati_stats(self.path)
        # 200,000 / 7 per day vs 700,000 / 30 per day.
        expected = (200000 / 7 - 700000 / 30) / (700000 / 30) * 100
        self.assertAlmostEqual(stats["change"]["sales_per_day"], expected)
        self.assertAlmostEqual(stats["short"]["avg_order_value"], 100000.0)

    def test_empty_week_has_no_order_value_or_share_change(self):
        write_orders_csv([order("3", "wati", days_ago(15))], self.path)
        change = build_wati_stats(self.path)["change"]
        self.assertEqual(change["sales_per_day"], -100.0)
        self.assertIsNone(change["avg_order_value"])
        self.assertIsNone(change["repurchase_sales_percentage"])

    def test_gender_breakdown_compares_shares(self):
        rows = {r["label"]: r for r in build_wati_stats(self.path)["gender"]}
        self.assertAlmostEqual(rows["Male"]["short_share"], 50.0)
        self.assertAlmostEqual(rows["Male"]["long_share"], 100000 / 700000 * 100)

    def test_page_renders(self):
        with mock.patch("app.routes.wati_stats.refresh_all_orders_if_needed"):
            response = self.client.get("/wati_stats")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("WATI Stats", html)
        self.assertIn("Last 30 days", html)
        self.assertIn("Last 7 days", html)
        self.assertIn('href="/wati_stats"', html)

    def leads(self, since, until):
        # 10 new leads and 5 returning customers per window, whatever its length.
        return {"new_leads": 10, "returning_customers": 5, "all_arrivals": 15,
                "date_from": since, "date_to": until}

    def test_conversion_divides_like_by_like(self):
        stats = build_wati_stats(self.path)
        cv = conversion_rates(stats, self.leads)
        # 30 days: 3 first-time of 4 orders; 7 days: 1 first-time of 2.
        self.assertAlmostEqual(cv["long"]["new_customer_rate"], 30.0)
        self.assertAlmostEqual(cv["long"]["all_rate"], 4 / 15 * 100)
        self.assertAlmostEqual(cv["short"]["new_customer_rate"], 10.0)
        self.assertAlmostEqual(cv["change"]["new_customer_rate"], -20.0)
        self.assertFalse(cv["suspect"])

    def test_conversion_asks_for_each_windows_exact_days(self):
        stats = build_wati_stats(self.path)
        asked = []
        conversion_rates(stats, lambda a, b: asked.append((a, b)) or self.leads(a, b))
        self.assertEqual(asked, [(days_ago(30), days_ago(0)), (days_ago(7), days_ago(0))])

    def test_conversion_reports_revive_errors(self):
        def fail(since, until):
            raise RoxyLeadsError("Revive rejected the API token (401). Check it in Settings.")
        cv = conversion_rates(build_wati_stats(self.path), fail)
        self.assertIn("401", cv["error"])

    def test_page_without_token_points_to_settings(self):
        with mock.patch("app.routes.wati_stats.refresh_all_orders_if_needed"):
            html = self.client.get("/wati_stats").get_data(as_text=True)
        self.assertIn("Settings → Roxy integration", html)

    def test_page_shows_conversion_with_token(self):
        set_secret("roxy_api_token", "test-token")
        with mock.patch("app.routes.wati_stats.refresh_all_orders_if_needed"), \
                mock.patch("app.routes.wati_stats.fetch_new_leads",
                           side_effect=lambda a, b, token, base: self.leads(a, b)) as fetch:
            html = self.client.get("/wati_stats").get_data(as_text=True)
        self.assertEqual(fetch.call_args.args[2], "test-token")
        self.assertIn("New-customer conversion", html)
        self.assertIn("30.0%", html)
        self.assertNotIn("test-token", html)

    def test_settings_store_the_token_encrypted(self):
        response = self.client.post("/options/roxy", data={"base_url": "", "api_token": "abc123"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_secret("roxy_api_token"), "abc123")
        from app.models import Option
        self.assertNotEqual(Option.query.filter_by(meta_key="roxy_api_token").first().meta_value, "abc123")

    def test_settings_page_shows_roxy_section(self):
        set_secret("roxy_api_token", "abcd1234")
        html = self.client.get("/options").get_data(as_text=True)
        self.assertIn("Roxy integration", html)
        self.assertIn("1234", html)  # masked tail only
        self.assertNotIn("abcd1234", html)

    def test_reveal_returns_the_stored_token(self):
        self.assertEqual(self.client.post("/options/roxy/reveal").status_code, 404)
        set_secret("roxy_api_token", "abcd1234")
        self.assertEqual(self.client.post("/options/roxy/reveal").get_json()["token"], "abcd1234")

    def test_connection_test_calls_revive_without_cache(self):
        self.assertEqual(self.client.post("/options/roxy/test").status_code, 400)
        set_secret("roxy_api_token", "abcd1234")
        with mock.patch("app.services.roxy_leads.fetch_new_leads",
                        side_effect=lambda a, b, t, u, use_cache: self.leads(a, b)) as fetch:
            data = self.client.post("/options/roxy/test").get_json()
        self.assertTrue(data["ok"])
        self.assertIn("10 new leads", data["message"])
        self.assertEqual(fetch.call_args.args[:2], (days_ago(7), days_ago(0)))
        self.assertFalse(fetch.call_args.kwargs["use_cache"])

    def test_connection_test_reports_a_bad_token(self):
        set_secret("roxy_api_token", "abcd1234")
        with mock.patch("app.services.roxy_leads.fetch_new_leads",
                        side_effect=RoxyLeadsError("Revive rejected the API token (401). Check it in Settings.")):
            response = self.client.post("/options/roxy/test")
        self.assertEqual(response.status_code, 400)
        self.assertIn("401", response.get_json()["message"])

    def test_requires_login(self):
        anon = self.app.test_client()
        self.assertNotEqual(anon.get("/wati_stats").status_code, 200)


class FetchNewLeadsTests(unittest.TestCase):
    def setUp(self):
        roxy_leads.clear_cache()

    def response(self, status, body):
        r = mock.Mock(status_code=status)
        r.json.return_value = body
        return r

    def ok(self, since="2026-11-01", until="2026-11-15", **over):
        return {"status": "ok", "new_leads": 40, "returning_customers": 10, "all_arrivals": 50,
                "date_from": since, "date_to": until, **over}

    def test_sends_custom_window_and_bearer_token(self):
        with mock.patch("app.services.roxy_leads.requests.get", return_value=self.response(200, self.ok())) as get:
            result = fetch_new_leads("2026-11-01", "2026-11-15", "tok", "https://roxy.example/")
        self.assertEqual(result["new_leads"], 40)
        url = get.call_args.args[0]
        self.assertEqual(url, "https://roxy.example/api/new_leads")
        self.assertEqual(get.call_args.kwargs["params"], {"period": "custom", "since": "2026-11-01", "until": "2026-11-15"})
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertNotIn("tok", url)

    def test_caches_answers(self):
        with mock.patch("app.services.roxy_leads.requests.get", return_value=self.response(200, self.ok())) as get:
            fetch_new_leads("2026-11-01", "2026-11-15", "tok")
            fetch_new_leads("2026-11-01", "2026-11-15", "tok")
        self.assertEqual(get.call_count, 1)

    def test_401_is_a_readable_error(self):
        with mock.patch("app.services.roxy_leads.requests.get",
                        return_value=self.response(401, {"status": "error", "message": "unauthorized"})):
            with self.assertRaisesRegex(RoxyLeadsError, "401"):
                fetch_new_leads("2026-11-01", "2026-11-15", "tok")

    def test_rejects_a_different_window(self):
        with mock.patch("app.services.roxy_leads.requests.get",
                        return_value=self.response(200, self.ok(until="2026-11-14"))):
            with self.assertRaisesRegex(RoxyLeadsError, "not 2026-11-01"):
                fetch_new_leads("2026-11-01", "2026-11-15", "tok")

    def test_network_failure_hides_details(self):
        import requests
        with mock.patch("app.services.roxy_leads.requests.get", side_effect=requests.ConnectionError("tok leaked?")):
            with self.assertRaises(RoxyLeadsError) as ctx:
                fetch_new_leads("2026-11-01", "2026-11-15", "tok")
        self.assertNotIn("tok", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
