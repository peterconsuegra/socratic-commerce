"""add the coupon code and redemption steps to the reconnect email template

Revision ID: f6b3c9e5d8a2
Revises: e5a2b8d4c7f1
Create Date: 2026-09-15

Builds on the HTML seeded by e5a2b8d4c7f1 rather than repeating it: a coupon
box and three "how to redeem" steps are inserted before the call to action.
Only a template still marked updated_by='seed' is touched, so an edit made in
the UI after the seed is never overwritten; in that case the migration prints
a notice and leaves the row alone.
"""
import importlib.util
import os
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "f6b3c9e5d8a2"
down_revision = "e5a2b8d4c7f1"
branch_labels = None
depends_on = None

# The campaign's WooCommerce coupon. Also written into step 3 of the
# instructions so the reader sees the exact string to type.
COUPON_CODE = "TU-CODIGO-AQUI"

STORE_STEP_URL = ("https://saveaplaya.org/?utm_source=email&utm_medium=sendgrid"
                  "&utm_campaign=reconnect_50off&utm_content=steps")

_ANCHOR = "          <!-- Call to action -->\n"

COUPON_BLOCK = """          <!-- Coupon and how to redeem it -->
          <tr>
            <td style="padding:8px 32px 8px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:2px dashed #3a7b78; border-radius:8px; background-color:#f7fbfa;">
                <tr>
                  <td align="center" style="padding:20px 16px 6px 16px; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">
                    TU CÓDIGO DE DESCUENTO
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 16px 20px 16px; font-family:'Courier New', Courier, monospace; font-size:28px; line-height:34px; font-weight:bold; letter-spacing:3px; color:#2f3e4e;">
                    __COUPON_CODE__
                  </td>
                </tr>
              </table>

              <p style="margin:20px 0 8px 0; font-size:15px; line-height:22px; font-weight:bold; color:#2f3e4e;">
                Cómo redimirlo en saveaplaya.org
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:15px; line-height:23px; color:#2f3e4e;">
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">1.</td>
                  <td style="padding:4px 0;">
                    Entra a <a href="__STORE_STEP_URL__" style="color:#3a7b78; font-weight:bold;">saveaplaya.org</a>
                    y agrega tu pack al carrito.
                  </td>
                </tr>
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">2.</td>
                  <td style="padding:4px 0;">
                    En el checkout, a la hora de hacer el pago, haz clic en el botón
                    <strong>“Haz clic aquí para introducir tu código”</strong>.
                  </td>
                </tr>
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">3.</td>
                  <td style="padding:4px 0;">
                    Escribe <strong>__COUPON_CODE__</strong>, pulsa <strong>Aplicar cupón</strong> y verás el
                    descuento reflejado antes de confirmar tu pago. El envío gratis se aplica solo.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

""".replace("__COUPON_CODE__", COUPON_CODE).replace("__STORE_STEP_URL__", STORE_STEP_URL)


def _seed():
    """The seed migration, loaded by path: it holds the base HTML and the name."""
    path = os.path.join(os.path.dirname(__file__), "e5a2b8d4c7f1_seed_reconnect_50off_email_template.py")
    spec = importlib.util.spec_from_file_location("seed_e5a2b8d4c7f1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_coupon(html: str) -> str:
    assert html.count(_ANCHOR) == 1, "call-to-action anchor missing from the seeded template"
    return html.replace(_ANCHOR, COUPON_BLOCK + _ANCHOR)


def _set_body(html: str):
    seed = _seed()
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id, updated_by FROM email_templates WHERE name = :name"),
        {"name": seed.TEMPLATE_NAME},
    ).first()
    if not row:
        return
    if row.updated_by != "seed":
        print(f"email template {seed.TEMPLATE_NAME!r} was edited in the UI (by {row.updated_by}); "
              "leaving it unchanged.")
        return
    conn.execute(
        sa.text("UPDATE email_templates SET html_body = :html, updated_at = :now WHERE id = :id"),
        {"html": html, "now": datetime.now(timezone.utc).replace(tzinfo=None), "id": row.id},
    )


def upgrade():
    _set_body(with_coupon(_seed().TEMPLATE_HTML))


def downgrade():
    _set_body(_seed().TEMPLATE_HTML)
