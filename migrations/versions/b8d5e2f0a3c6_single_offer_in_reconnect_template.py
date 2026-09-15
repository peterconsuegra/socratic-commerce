"""replace the six promotions with the single Pack Favorito offer in the reconnect template

Revision ID: b8d5e2f0a3c6
Revises: a7c4d1e9f2b5
Create Date: 2026-09-16

Unlike f6b3c9e5d8a2 this applies even to a template edited in the UI: it
swaps only the promotions section, delimited by the HTML comment markers the
seed left in the body, so the coupon code, hero image and any other edit
survive. If the markers are gone the template is left alone and a notice is
printed.
"""
import importlib.util
import os
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "b8d5e2f0a3c6"
down_revision = "a7c4d1e9f2b5"
branch_labels = None
depends_on = None

PROMOTIONS_MARKER = "<!-- Promotions -->"
OFFER_MARKER = "<!-- Offer -->"

OFFER_BLOCK = """<!-- Offer -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#e6f2f1; border-radius:8px;">
                <tr>
                  <td align="center" style="padding:22px 20px 6px 20px; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">
                    SOLO POR HOY
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 2px 20px; font-size:24px; line-height:30px; font-weight:bold; color:#2f3e4e;">
                    Pack Favorito · 3 und
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 6px 20px; font-size:28px; line-height:36px; color:#2f3e4e;">
                    <strong>$52.500</strong>
                    <span style="font-size:17px; color:#8a97a3; text-decoration:line-through; white-space:nowrap;">$105.000</span>
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 22px 20px; font-size:18px; line-height:26px; font-weight:bold; color:#3a7b78;">
                    50% OFF con envío gratis
                  </td>
                </tr>
              </table>
              <p style="margin:12px 0 0 0; font-size:13px; line-height:20px; color:#6b7883;">
                Envío gratis vía Coordinadora y Envía.
              </p>
            </td>
          </tr>

          """

# The intro sentence spoke of several packs; with one offer it names it.
INTRO_OLD = "Hoy te dejamos los packs a mitad de precio para ti y tu familia, solo por este correo."
INTRO_NEW = "Hoy te dejamos el Pack Favorito a mitad de precio para ti y tu familia, solo por este correo."


def _seed():
    path = os.path.join(os.path.dirname(__file__), "e5a2b8d4c7f1_seed_reconnect_50off_email_template.py")
    spec = importlib.util.spec_from_file_location("seed_e5a2b8d4c7f1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(html: str, marker: str) -> tuple[int, int] | None:
    """Span of the section that starts at `marker` and runs up to the next
    HTML comment, or None when the marker is absent."""
    start = html.find(marker)
    if start < 0:
        return None
    end = html.find("<!-- ", start + len(marker))
    return start, (end if end >= 0 else len(html))


def _promotions_block(seed_html: str) -> str:
    span = _section(seed_html, PROMOTIONS_MARKER)
    assert span, "seed template lost its promotions marker"
    return seed_html[span[0]:span[1]]


def with_single_offer(html: str) -> str | None:
    span = _section(html, PROMOTIONS_MARKER)
    if not span:
        return None
    html = html[:span[0]] + OFFER_BLOCK + html[span[1]:]
    return html.replace(INTRO_OLD, INTRO_NEW)


def with_promotions(html: str, seed_html: str) -> str | None:
    span = _section(html, OFFER_MARKER)
    if not span:
        return None
    html = html[:span[0]] + _promotions_block(seed_html) + html[span[1]:]
    return html.replace(INTRO_NEW, INTRO_OLD)


def _rewrite(transform):
    seed = _seed()
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id, html_body FROM email_templates WHERE name = :name"),
        {"name": seed.TEMPLATE_NAME},
    ).first()
    if not row:
        return
    new_html = transform(row.html_body, seed.TEMPLATE_HTML)
    if new_html is None:
        print(f"email template {seed.TEMPLATE_NAME!r} no longer has the section marker; "
              "leaving it unchanged.")
        return
    conn.execute(
        sa.text("UPDATE email_templates SET html_body = :html, updated_at = :now WHERE id = :id"),
        {"html": new_html, "now": datetime.now(timezone.utc).replace(tzinfo=None), "id": row.id},
    )


def upgrade():
    _rewrite(lambda html, _seed_html: with_single_offer(html))


def downgrade():
    _rewrite(with_promotions)
