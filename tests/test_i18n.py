"""
English/Spanish: the site-wide language set on /options, the _() helper in
every template, and the Spanish strings in app/i18n.py.

In-memory SQLite; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import ast
import os
import re
import string
import unittest

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from flask import render_template_string  # noqa: E402

from app import create_app, db  # noqa: E402
from app.i18n import LANGUAGE_OPTION_KEY, SPANISH, site_language, translate  # noqa: E402
from app.models import Option, User  # noqa: E402

TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "app", "templates")
CALL = re.compile(r"""\b_\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*[,)]""")


def template_keys():
    """Every literal key passed to _() in a template, with the file it's in."""
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith(".html"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    for m in CALL.finditer(f.read()):
                        yield os.path.relpath(path, TEMPLATES), ast.literal_eval(m.group(1))


def fields(text):
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


class LanguageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
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

    def set_language(self, value):
        db.session.add(Option(meta_key=LANGUAGE_OPTION_KEY, meta_value=value))
        db.session.commit()

    def page(self, path="/options"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)


class SiteLanguageTests(LanguageTestCase):
    def test_english_by_default(self):
        html = self.page()
        self.assertEqual(site_language(), "en")
        self.assertIn('<html lang="en">', html)
        self.assertIn('class="active">Settings</a>', html)
        self.assertIn(">Monthly Sales</a>", html)
        self.assertIn("Platform language", html)

    def test_spanish_option_translates_the_sidebar_and_the_page(self):
        self.set_language("es")
        html = self.page()
        self.assertIn('<html lang="es">', html)
        # Sidebar and header
        self.assertIn('class="active">Configuración</a>', html)
        self.assertIn(">Ventas mensuales</a>", html)
        self.assertIn('<div class="nav-group-label">Retención</div>', html)
        self.assertIn("Cerrar sesión", html)
        self.assertNotIn(">Monthly Sales</a>", html)
        # The page's own labels
        self.assertIn("Idioma de la plataforma", html)
        self.assertNotIn("Platform language", html)
        self.assertIn('<option value="es" selected>Español</option>', html)

    def test_saving_the_option_switches_every_page(self):
        response = self.client.post("/options/language", data={"language": "es"}, follow_redirects=True)
        html = response.get_data(as_text=True)
        self.assertIn("Idioma guardado: Español.", html)
        self.assertEqual(Option.query.filter_by(meta_key=LANGUAGE_OPTION_KEY).one().meta_value, "es")
        self.assertIn(">Ventas diarias</a>", self.page("/settings"))

        self.client.post("/options/language", data={"language": "en"})
        self.assertEqual(site_language(), "en")
        self.assertIn(">Daily Sales</a>", self.page("/settings"))

    def test_an_unknown_language_is_refused_and_an_odd_stored_value_reads_english(self):
        html = self.client.post("/options/language", data={"language": "fr"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Pick English or Spanish.", html)
        self.assertIsNone(Option.query.filter_by(meta_key=LANGUAGE_OPTION_KEY).first())

        self.set_language("fr")
        self.assertIn('<html lang="en">', self.page())

    def test_the_api_is_never_translated(self):
        english = self.client.get("/api/health").get_json()
        self.set_language("es")
        self.assertEqual(self.client.get("/api/health").get_json(), english)


class FallbackTests(LanguageTestCase):
    def test_a_missing_key_falls_back_to_english(self):
        self.assertEqual(translate("Total sales", "es"), "Ventas totales")
        self.assertEqual(translate("Not translated yet", "es"), "Not translated yet")
        self.assertEqual(translate("Total sales", "en"), "Total sales")

        self.set_language("es")
        with self.app.test_request_context("/"):
            self.assertEqual(
                render_template_string('{{ _("Total sales") }} · {{ _("Not translated yet") }}'),
                "Ventas totales · Not translated yet",
            )

    def test_outside_a_request_text_reads_english(self):
        self.set_language("es")
        self.assertEqual(translate("Total sales"), "Total sales")


class CatalogTests(unittest.TestCase):
    def test_every_template_key_has_spanish(self):
        missing = sorted({f"{path}: {key!r}" for path, key in template_keys() if key not in SPANISH})
        self.assertEqual(missing, [], "add these to SPANISH in app/i18n.py")

    def test_placeholders_match(self):
        bad = [k for k, v in SPANISH.items() if fields(k) != fields(v)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
