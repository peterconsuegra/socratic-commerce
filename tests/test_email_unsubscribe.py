"""
Email opt-out: the unsubscribe page, the switch it flips, and the sends and
listings that honour it.

Runs against an in-memory SQLite database with SendGrid replaced by a
recorder, so nothing is sent and no real data is touched:

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import CustomerContact, EmailSubscription, EmailTemplate, User  # noqa: E402
from app.services import email_subscriptions as subs  # noqa: E402
from app.services.recurrent_customers import get_recurrent_customers  # noqa: E402
from app.services.sendgrid import html_to_text  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SENDGRID_CONFIG = {"api_key": "SG.test", "from_email": "hola@example.com", "from_name": "Save a Playa",
                   "reply_to": "", "asm_group_id": None}

TEMPLATE_HTML = ('<p>Hola {{name}}</p>'
                 '<p><a href="{{unsubscribe_url}}">Cancelar suscripción</a></p>')

ONE_CLICK = {"List-Unsubscribe": "One-Click"}


def customer(email, name="Ana"):
    """A row shaped like get_recurrent_customers output."""
    return {"email": email, "name": name, "last_name": "", "phone": "", "last_skus": ["pack_favorito"],
            "days_since_last_order": 120, "orders_count": 2, "total_spent": 90000.0,
            "last_order": "2026-05-30", "last_order_utc": "2026-05-30T15:00:00Z"}


def unsubscribe(email):
    token = subs.tokens_for([email])[email]
    subs.set_unsubscribed(subs.subscription_by_token(token), True, EmailSubscription.SOURCE_PAGE)


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.headers = {"X-Message-Id": "msg-1"}
        self.text = ""

    def json(self):
        return {"errors": [{"message": "rejected"}]} if self.status_code >= 400 else {}


class FakeSendGrid:
    """Stands in for requests.Session: records each mail/send payload and
    answers with the queued statuses (202 once they run out)."""

    def __init__(self, statuses=()):
        self.payloads = []
        self._statuses = list(statuses)

    def __call__(self):
        return self

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return FakeResponse(self._statuses.pop(0) if self._statuses else 202)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login(self):
        user = User(username="operator@example.com", password_hash="unused")
        db.session.add(user)
        db.session.commit()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True


class TokenTests(AppTestCase):
    def test_one_row_and_token_per_address(self):
        first = subs.tokens_for(["Ana@Example.com ", "ana@example.com", "luis@example.com", "not-an-email", ""])
        self.assertEqual(set(first), {"ana@example.com", "luis@example.com"})
        self.assertNotEqual(first["ana@example.com"], first["luis@example.com"])

        # Reused on every later send, so an old email's link keeps working.
        self.assertEqual(subs.tokens_for(["ANA@example.com"]), {"ana@example.com": first["ana@example.com"]})
        self.assertEqual(EmailSubscription.query.count(), 2)
        self.assertEqual(EmailSubscription.query.filter_by(unsubscribed=False).count(), 2)


class UnsubscribePageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.token = subs.tokens_for(["maria@example.com"])["maria@example.com"]
        self.url = f"/unsubscribe/{self.token}"

    def sub(self):
        return EmailSubscription.query.filter_by(email="maria@example.com").one()

    def test_opening_the_link_only_asks_for_confirmation(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)  # public: no redirect to the login page
        page = resp.get_data(as_text=True)
        self.assertIn("Sí, cancelar mi suscripción", page)
        self.assertIn("ma•••@example.com", page)
        self.assertNotIn("maria@example.com", page)
        self.assertFalse(self.sub().unsubscribed)

    def test_confirm_button_turns_the_switch_on(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Listo, cancelamos tu suscripción", resp.get_data(as_text=True))
        sub = self.sub()
        self.assertTrue(sub.unsubscribed)
        self.assertEqual(sub.unsubscribe_source, EmailSubscription.SOURCE_PAGE)
        self.assertIsNotNone(sub.unsubscribed_at)
        self.assertEqual(subs.unsubscribed_emails(), {"maria@example.com"})

    def test_mailbox_one_click_post_turns_the_switch_on(self):
        resp = self.client.post(self.url, data=ONE_CLICK)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.sub().unsubscribed)
        self.assertEqual(self.sub().unsubscribe_source, EmailSubscription.SOURCE_ONE_CLICK)

    def test_second_click_keeps_the_first_date_and_source(self):
        self.client.post(self.url, data=ONE_CLICK)
        first = self.sub().unsubscribed_at
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual((self.sub().unsubscribed_at, self.sub().unsubscribe_source),
                         (first, EmailSubscription.SOURCE_ONE_CLICK))
        self.assertIn("Tu suscripción ya está cancelada", self.client.get(self.url).get_data(as_text=True))

    def test_resubscribe_turns_the_switch_back_off(self):
        self.client.post(self.url)
        resp = self.client.post(f"{self.url}/resubscribe")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Volverás a recibir nuestros correos", resp.get_data(as_text=True))
        sub = self.sub()
        self.assertFalse(sub.unsubscribed)
        self.assertIsNone(sub.unsubscribed_at)
        self.assertEqual(subs.unsubscribed_emails(), set())

    def test_unknown_or_malformed_tokens_change_nothing(self):
        for path in ("/unsubscribe/" + "0" * 32, "/unsubscribe/not-a-token", "/unsubscribe/" + self.token[:-1]):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.client.post(path, data=ONE_CLICK).status_code, 404)
                self.assertEqual(self.client.post(path + "/resubscribe").status_code, 404)
        self.assertFalse(self.sub().unsubscribed)

    def test_page_is_kept_out_of_caches_search_engines_and_referers(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", resp.headers["X-Robots-Tag"])


class SendTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.template = EmailTemplate(name="Reconecta", subject="Hola {{name}}", html_body=TEMPLATE_HTML)
        db.session.add(self.template)
        db.session.commit()
        self.sendgrid = FakeSendGrid()

    def send(self, rows, missing=(), config=None, statuses=()):
        self.sendgrid = FakeSendGrid(statuses)
        emails = [r["email"] for r in rows] + list(missing)
        with mock.patch("app.routes.lapsed._resolve_selected", return_value=(rows, list(missing))), \
                mock.patch("app.routes.lapsed.get_sendgrid_config", return_value=config or SENDGRID_CONFIG), \
                mock.patch("app.services.sendgrid.requests.Session", self.sendgrid):
            return self.client.post("/reconnect-lapsed-customers-by-email/send",
                                    json={"emails": emails, "template_id": self.template.id})

    def url_for(self, email):
        return "http://localhost/unsubscribe/" + subs.tokens_for([email])[email]

    def test_every_recipient_gets_their_own_link_and_one_click_headers(self):
        resp = self.send([customer("Ana@Example.com"), customer("luis@example.com", "Luis")])
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["sent"], 2)

        (payload,) = self.sendgrid.payloads
        for personalization, email in zip(payload["personalizations"], ["ana@example.com", "luis@example.com"]):
            url = self.url_for(email)
            self.assertEqual(personalization["substitutions"]["{{unsubscribe_url}}"], url)
            self.assertEqual(personalization["headers"], {
                "List-Unsubscribe": f"<{url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            })
        self.assertEqual(EmailSubscription.query.filter_by(unsubscribed=False).count(), 2)

    def test_unsubscribed_customer_is_skipped_and_reported(self):
        unsubscribe("ana@example.com")
        resp = self.send([customer("Ana@Example.com"), customer("luis@example.com", "Luis")])
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200, data)
        self.assertEqual((data["sent"], data["skipped"]), (1, 1))
        self.assertIn({"email": "ana@example.com", "reason": "unsubscribed"}, data["skipped_detail"])

        recipients = [p["to"][0]["email"] for p in self.sendgrid.payloads[0]["personalizations"]]
        self.assertEqual(recipients, ["luis@example.com"])
        # Only the email that went out is logged as a contact.
        self.assertEqual([c.email for c in CustomerContact.query.all()], ["luis@example.com"])

    def test_nothing_is_sent_when_no_selected_customer_can_be_emailed(self):
        unsubscribe("ana@example.com")
        resp = self.send([customer("ana@example.com")], missing=["gone@example.com"])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("1 unsubscribed, 1 not found", resp.get_json()["message"])
        self.assertEqual(self.sendgrid.payloads, [])

    def test_template_without_an_unsubscribe_link_cannot_go_to_customers(self):
        self.template.html_body = "<p>Hola {{name}}</p>"
        db.session.commit()
        resp = self.send([customer("ana@example.com")])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("{{unsubscribe_url}}", resp.get_json()["message"])
        self.assertEqual(self.sendgrid.payloads, [])

    def test_with_an_unsubscribe_group_sendgrid_supplies_the_header(self):
        resp = self.send([customer("ana@example.com")], config={**SENDGRID_CONFIG, "asm_group_id": 4242})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        payload = self.sendgrid.payloads[0]
        self.assertEqual(payload["asm"], {"group_id": 4242})
        self.assertNotIn("headers", payload["personalizations"][0])
        # The footer link is still the app's own page.
        self.assertEqual(payload["personalizations"][0]["substitutions"]["{{unsubscribe_url}}"],
                         self.url_for("ana@example.com"))

    def test_one_by_one_fallback_renders_the_link_into_both_parts(self):
        # SendGrid rejects the batch (400), so the recipient is re-sent alone.
        resp = self.send([customer("ana@example.com")], statuses=[400, 202])
        self.assertEqual(resp.get_json()["sent"], 1)
        single = self.sendgrid.payloads[1]
        url = self.url_for("ana@example.com")
        parts = {c["type"]: c["value"] for c in single["content"]}
        self.assertIn(f'href="{url}"', parts["text/html"])
        self.assertIn(f"Cancelar suscripción ({url})", parts["text/plain"])
        self.assertEqual(single["personalizations"][0]["headers"]["List-Unsubscribe"], f"<{url}>")

    def test_test_send_links_the_test_address_and_is_never_blocked(self):
        self.template.html_body = "<p>Hola {{name}}</p>"  # no link: fine for a test send
        db.session.commit()
        with mock.patch("app.routes.email_templates.get_sendgrid_config", return_value=SENDGRID_CONFIG), \
                mock.patch("app.services.sendgrid.requests.Session", self.sendgrid):
            resp = self.client.post(f"/email-templates/{self.template.id}/send-test", json={"to": "Me@Example.com"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        headers = self.sendgrid.payloads[0]["personalizations"][0]["headers"]
        self.assertEqual(headers["List-Unsubscribe"], f"<{self.url_for('me@example.com')}>")


ORDERS_CSV = """email,name,order_date,order_date_utc,total_value,sku,phone,purchase_number,is_repurchase
ana@example.com,Ana,2025-01-10 10:00:00,2025-01-10T15:00:00Z,50000,pack_favorito,,1,false
Luis@Example.com,Luis,2025-02-10 10:00:00,2025-02-10T15:00:00Z,60000,pack_favorito,,1,false
eva@example.com,Eva,2025-03-10 10:00:00,2025-03-10T15:00:00Z,70000,una_unidad,,1,false
"""


class ListingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        fd, self.csv_path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as f:
            f.write(ORDERS_CSV)

    def tearDown(self):
        os.remove(self.csv_path)
        super().tearDown()

    def test_segment_drops_unsubscribed_and_counts_only_its_own(self):
        result = get_recurrent_customers(orders_csv_path=self.csv_path, min_orders=1, sku_filter={"pack_favorito"},
                                         unsubscribed_emails={"luis@example.com", "eva@example.com"})
        self.assertEqual([r["email"] for r in result["rows"]], ["ana@example.com"])
        # Eva is outside this SKU segment, so only Luis counts as hidden here.
        self.assertEqual(result["summary"]["unsubscribed"], 1)

    def test_email_page_hides_them_and_the_whatsapp_page_does_not(self):
        self.login()
        unsubscribe("luis@example.com")
        self.app.config["ALL_ORDERS_CSV"] = self.csv_path
        with mock.patch("app.routes.lapsed.refresh_all_orders_if_needed"):
            email_page = self.client.get("/reconnect-lapsed-customers-by-email?sku=pack_favorito")
            wati_page = self.client.get("/lapsed-customers?sku=pack_favorito")
        self.assertEqual((email_page.status_code, wati_page.status_code), (200, 200))
        email_html = email_page.get_data(as_text=True)
        self.assertIn("ana@example.com", email_html)
        self.assertNotIn("luis@example.com", email_html.lower())
        self.assertIn("1 customer hidden: unsubscribed", email_html)
        self.assertIn("luis@example.com", wati_page.get_data(as_text=True).lower())


class PlainTextTests(unittest.TestCase):
    def test_links_keep_their_address_in_the_plain_text_part(self):
        text = html_to_text(
            '<p>Hola</p><p><a href="https://x.co/u/abc" style="color:#8a97a3">Cancelar <b>suscripción</b></a></p>'
            '<a href="https://saveaplaya.org"><img src="hero.jpg"></a> <a href="#top">Arriba</a>'
        )
        self.assertIn("Cancelar suscripción (https://x.co/u/abc)", text)
        self.assertIn("https://saveaplaya.org", text)
        self.assertIn("Arriba", text)
        self.assertNotIn("#top", text)


class MigrationTests(unittest.TestCase):
    """The deploy runs `flask db upgrade`; run the real chain on a scratch file."""

    def flask_db(self, db_path, *args):
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
        subprocess.run([sys.executable, "-m", "flask", "--app", "wsgi", "db", *args],
                       cwd=PROJECT_ROOT, env=env, check=True, capture_output=True)

    def test_upgrade_links_the_seeded_template_and_downgrade_restores_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "migrate.db")

            def body():
                with closing(sqlite3.connect(path)) as conn:
                    return conn.execute("SELECT html_body FROM email_templates").fetchone()[0]

            def tables():
                with closing(sqlite3.connect(path)) as conn:
                    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

            self.flask_db(path, "upgrade")
            self.assertIn("email_subscriptions", tables())
            self.assertIn('href="{{unsubscribe_url}}"', body())
            self.assertNotIn("asm_group_unsubscribe_raw_url", body())

            self.flask_db(path, "downgrade", "e8b2d6f4a1c7")
            self.assertIn('href="<%asm_group_unsubscribe_raw_url%>"', body())

            self.flask_db(path, "downgrade", "d0f7a4b2c5e8")
            self.assertNotIn("email_subscriptions", tables())

            self.flask_db(path, "upgrade")  # and forward again cleanly
            self.assertIn('href="{{unsubscribe_url}}"', body())


if __name__ == "__main__":
    unittest.main()
