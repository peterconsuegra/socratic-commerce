"""
/inventory: tubes sold in a date range, every pack times the tubes it holds.

Runs in a temporary working directory (the page reads its orders from
./data) against an in-memory SQLite database; nothing reaches the store.

    venv/bin/python -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest

# Set before any app is created, so no test can reach a configured database.
os.environ["DATABASE_URL"] = "sqlite://"

from app import create_app, db  # noqa: E402
from app.models import Option, User  # noqa: E402
from app.services.get_data import write_orders_csv  # noqa: E402
from app.services.inventory import build_inventory, tubes_from_product_name  # noqa: E402
from app.services.periods import now_bogota_naive  # noqa: E402

PATH = "data/inventory_orders.csv"


def order(order_id, sku, product="", day="2026-09-01 10:00:00", total="60000.00"):
    """One order shaped like the API's JSON."""
    return {
        "order_id": order_id, "order_date": day, "name": "Ana", "gender": "female",
        "email": f"c{order_id}@x.com", "city": "BOGOTA (C/MARCA)", "state": "Cundinamarca",
        "total_value": total, "sku": sku, "product": product, "utm_source": "facebook",
        "purchase_number": 1, "is_repurchase": False,
    }


ORDERS = [
    order("1", "pack_favorito", "Pack Ahorro Favorito (3 unidades)"),
    order("2", "pack_favorito", "Pack Ahorro Favorito (3 unidades)", day="2026-09-03 18:00:00"),
    # Two packs in one order: both count.
    order("3", "una_unidad, pack_valentin", "Desodorante natural 1 unidad"),
    # The SKU wins over a product name that says otherwise.
    order("4", "pack_believer", "Desodorante Natural Unisex Save a Playa. Promoción: (2X1)"),
    # No SKU: counted from the product name.
    order("5", "", "Promoción Emprendedor: (12 unidades en caja individual)"),
    # Unknown: listed, not counted.
    order("6", "pack_nuevo", "Pack Nuevo"),
    order("7", "", "Unnamed Product"),
]


class TempOrdersTestCase(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("data")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)


class ProductNameTests(unittest.TestCase):
    def test_reads_the_tubes_a_name_states(self):
        cases = {
            "Promoción Regala Salud: (10 unidades)": 10,
            "Dos (2) Unidades de Desodorante Natural Unisex Save a Playa. 25% de descuento": 2,
            "Desodorante Natural Unisex Save a Playa. Promoción: (2X1)": 2,
            "Desodorante Natural Save A Playa. Promoción: (3X1)": 3,
            "Promoción Empresa: (24 unidades + ecostand)": 24,
            "Gafas retro polarizadas con protección UV400 + 4 tubos de desodorante": 4,
            "Desodorante Save A Playa": 1,
        }
        for name, tubes in cases.items():
            self.assertEqual(tubes_from_product_name(name), tubes, name)

    def test_unknown_when_the_name_does_not_say(self):
        for name in ("Unnamed Product", "Desodorante Natural Unisex Save a Playa. Formula 3X", ""):
            self.assertIsNone(tubes_from_product_name(name), name)


class BuildInventoryTests(TempOrdersTestCase):
    def test_counts_every_pack_times_its_tubes(self):
        write_orders_csv(ORDERS, PATH)
        result = build_inventory(PATH)
        packs = {p["pack"]: (p["tubes_per_pack"], p["packs"], p["tubes"]) for p in result["packs"]}
        self.assertEqual(packs, {
            "pack_favorito": (3, 2, 6),
            "una_unidad": (1, 1, 1),
            "pack_valentin": (2, 1, 2),
            "pack_believer": (12, 1, 12),
            "Promoción Emprendedor: (12 unidades en caja individual)": (12, 1, 12),
        })
        s = result["summary"]
        self.assertEqual((s["tubes"], s["packs"], s["orders"], s["counted_orders"]), (33, 6, 7, 5))
        self.assertEqual((s["first_day"], s["last_day"], s["days"]), ("2026-09-01", "2026-09-03", 3))
        self.assertAlmostEqual(s["tubes_per_day"], 11.0)
        self.assertEqual(result["packs"][0]["pack"], "pack_believer")  # most tubes first

    def test_lists_packs_whose_tubes_are_unknown(self):
        write_orders_csv(ORDERS, PATH)
        result = build_inventory(PATH)
        self.assertEqual(result["summary"]["unmatched_packs"], 2)
        self.assertEqual({u["pack"] for u in result["unmatched"]}, {"pack_nuevo", "Unnamed Product"})

    def test_todays_orders_count_only_when_the_range_runs_through_today(self):
        today = f"{now_bogota_naive():%Y-%m-%d} 00:00:01"
        write_orders_csv([order("1", "pack_favorito"), order("2", "pack_believer", day=today)], PATH)
        self.assertEqual(build_inventory(PATH)["summary"]["tubes"], 3)
        self.assertEqual(build_inventory(PATH, include_today=True)["summary"]["tubes"], 15)

    def test_none_without_orders(self):
        write_orders_csv([], PATH)
        self.assertIsNone(build_inventory(PATH))


class InventoryPageTests(TempOrdersTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username="operator@example.com", password_hash="unused")
        db.session.add(user)
        db.session.add_all([
            Option(meta_key="start_date_inventory_orders.csv", meta_value="01/09/2026"),
            Option(meta_key="end_date_inventory_orders.csv", meta_value="30/09/2026"),
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

    def get(self):
        response = self.client.get("/inventory")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_asks_for_a_range_before_any_is_fetched(self):
        html = self.get()
        self.assertIn("Fetch Data", html)
        self.assertIn("to count the tubes sold", html)
        self.assertIn('value="inventory_orders.csv"', html)

    def test_shows_the_tubes_sold_in_the_range(self):
        write_orders_csv(ORDERS, PATH)
        html = self.get()
        self.assertIn("From 01/09/2026 to 30/09/2026", html)
        self.assertIn("<code>pack_believer</code>", html)
        self.assertIn(">33<", html)
        self.assertIn("2 packs not counted", html)

    def test_listed_in_the_menu(self):
        self.assertIn('href="/inventory"', self.get())


if __name__ == "__main__":
    unittest.main()
