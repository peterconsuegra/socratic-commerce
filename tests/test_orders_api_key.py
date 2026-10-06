"""
The store's orders API key is stored encrypted in the options table: set on
the Settings page, read decrypted by the sync, never shown in clear.

Runs against an in-memory SQLite database (a scratch file for the migration)
with the orders API faked, so nothing reaches the store:

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
from app.models import Option, User  # noqa: E402
from app.routes.common import ORDERS_API_KEY_KEY, build_orders_csv  # noqa: E402
from app.services import get_data  # noqa: E402
from app.services.secrets import decrypt, get_secret  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "test-orders-key-WXYZ"


class OrdersApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
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

    def save(self, api_key):
        return self.client.post("/options/orders-api",
                                data={"orders_url": "https://store.test/orders", "api_key": api_key})

    def stored(self):
        return Option.query.filter_by(meta_key=ORDERS_API_KEY_KEY).first().meta_value

    def test_saving_stores_the_key_encrypted(self):
        self.save(KEY)
        self.assertNotIn(KEY, self.stored())
        self.assertEqual(get_secret(ORDERS_API_KEY_KEY), KEY)
        self.assertEqual(Option.query.filter_by(meta_key="orders_url").first().meta_value,
                         "https://store.test/orders")

    def test_a_blank_key_field_keeps_the_stored_key(self):
        self.save(KEY)
        self.save("")
        self.assertEqual(get_secret(ORDERS_API_KEY_KEY), KEY)

    def test_settings_never_show_the_key_or_its_ciphertext(self):
        self.save(KEY)
        html = self.client.get("/options").get_data(as_text=True)
        self.assertNotIn(KEY, html)
        self.assertNotIn(self.stored(), html)
        self.assertIn("••••••••WXYZ", html)  # the masked hint: last 4 characters only

        # The generic option editor refuses it: a value typed there would be
        # stored unencrypted.
        option_id = Option.query.filter_by(meta_key=ORDERS_API_KEY_KEY).first().id
        self.assertEqual(self.client.get(f"/options/{option_id}/edit").status_code, 302)
        self.client.post(f"/options/{option_id}/edit", data={"meta_value": "plain"})
        self.assertEqual(get_secret(ORDERS_API_KEY_KEY), KEY)

    def test_reveal_returns_the_key_on_demand(self):
        self.save(KEY)
        self.assertEqual(self.client.post("/options/orders-api/reveal").get_json()["api_key"], KEY)

    def test_the_sync_sends_the_decrypted_key(self):
        self.save(KEY)
        sent = []

        def fake_get(url, headers=None, params=None, timeout=None):
            sent.append(headers["Authorization"])
            return mock.Mock(status_code=200, raise_for_status=lambda: None,
                             json=lambda: [{"order_id": "1", "order_date": "2026-09-02 10:00:00"}])

        with tempfile.TemporaryDirectory() as tmp:
            self.app.config.update(PROJECT_ROOT=tmp, DATA_DIR=os.path.join(tmp, "data"))
            with mock.patch.object(get_data.requests, "get", fake_get):
                build_orders_csv(file_name="test_orders.csv", start_date="01/09/2026", end_date="30/09/2026")
        self.assertEqual(sent, [f"Bearer {KEY}"])


class MigrationTests(unittest.TestCase):
    """The deploy runs `flask db upgrade`; run the real chain on a scratch file."""

    def flask_db(self, db_path, *args):
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
        subprocess.run([sys.executable, "-m", "flask", "--app", "wsgi", "db", *args],
                       cwd=PROJECT_ROOT, env=env, check=True, capture_output=True)

    def test_upgrade_encrypts_a_plain_key_once_and_downgrade_restores_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "migrate.db")

            def value():
                with closing(sqlite3.connect(path)) as conn:
                    return conn.execute("SELECT meta_value FROM options WHERE meta_key = 'api_key'").fetchone()[0]

            def set_value(v):
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute("UPDATE options SET meta_value = ? WHERE meta_key = 'api_key'", (v,))
                    conn.commit()

            self.flask_db(path, "upgrade", "f3c7a9e1b5d2")
            set_value(KEY)
            self.flask_db(path, "upgrade")
            encrypted = value()
            self.assertNotEqual(encrypted, KEY)
            self.assertEqual(decrypt(encrypted), KEY)

            self.flask_db(path, "downgrade", "f3c7a9e1b5d2")
            self.assertEqual(value(), KEY)

            # A key already saved encrypted (say, through Settings) is not
            # encrypted a second time.
            set_value(encrypted)
            self.flask_db(path, "upgrade")
            self.assertEqual(value(), encrypted)


if __name__ == "__main__":
    unittest.main()
