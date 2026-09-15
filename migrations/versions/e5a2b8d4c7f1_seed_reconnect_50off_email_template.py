"""seed the "Reconecta · 50% OFF + envío gratis" email template

Revision ID: e5a2b8d4c7f1
Revises: d4f1a7c9e2b3
Create Date: 2026-09-15

A data migration, like a819ed7b61f4 for the initial options: it is the only
path that lands content on the deployed app without a login. Idempotent - it
inserts the template once, by name, and never overwrites edits made later in
the UI. The creative is served by the app itself from /static/email so mail
clients can load it from an absolute URL.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "e5a2b8d4c7f1"
down_revision = "d4f1a7c9e2b3"
branch_labels = None
depends_on = None

TEMPLATE_NAME = "Reconecta · 50% OFF + envío gratis"
TEMPLATE_SUBJECT = "¡Hola! {{name}} 💙 solo por hoy tienes 50% OFF + envío gratis 🌊"

IMAGE_URL = "https://web-production-c6d52.up.railway.app/static/email/rosa-fresca-vs-marchita.jpg"
STORE_URL = ("https://saveaplaya.org/?utm_source=email&utm_medium=sendgrid"
             "&utm_campaign=reconnect_50off&utm_content=cta")

TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Solo por hoy: 50% OFF + envío gratis</title>
</head>
<body style="margin:0; padding:0; background-color:#f2f5f6; font-family:Arial, Helvetica, sans-serif; color:#2f3e4e;">
  <!-- Preheader: shown next to the subject in the inbox, hidden in the body -->
  <div style="display:none; max-height:0; overflow:hidden; font-size:1px; line-height:1px; color:#f2f5f6;">
    Packs a mitad de precio y envío gratis, solo por hoy.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f2f5f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:600px; background-color:#ffffff; border-radius:8px; overflow:hidden;">

          <!-- Brand bar -->
          <tr>
            <td align="center" style="padding:18px 24px; background-color:#3a7b78; color:#ffffff; font-size:18px; font-weight:bold; letter-spacing:2px;">
              SAVE A PLAYA
            </td>
          </tr>

          <!-- Hero image -->
          <tr>
            <td style="padding:0; line-height:0;">
              <a href="__STORE_URL__" style="text-decoration:none;">
                <img src="__IMAGE_URL__" width="600" alt="Otros: aluminio y parabenos (rosa marchita). Save a Playa: sin agresiones (rosa fresca). Envío gratis."
                     style="display:block; width:100%; max-width:600px; height:auto; border:0;">
              </a>
            </td>
          </tr>

          <!-- Greeting -->
          <tr>
            <td style="padding:28px 32px 8px 32px;">
              <h1 style="margin:0 0 6px 0; font-size:24px; line-height:30px; color:#2f3e4e;">
                ¡Hola, {{name}}! 💙
              </h1>
              <p style="margin:0; font-size:18px; line-height:26px; color:#3a7b78; font-weight:bold;">
                Solo por hoy tienes 50% OFF + envío gratis 🌊
              </p>
            </td>
          </tr>

          <!-- Body copy -->
          <tr>
            <td style="padding:12px 32px 8px 32px; font-size:16px; line-height:25px; color:#2f3e4e;">
              <p style="margin:0 0 14px 0;">
                Tu piel tiene dos caminos: el de la rosa marchita o el de la rosa fresca.
              </p>
              <p style="margin:0 0 14px 0;">
                Los desodorantes con aluminio y parabenos agreden tu axila todos los días;
                <strong>Save a Playa</strong> la cuida con aceite de coco, vitamina E y plata antibacterial.
              </p>
              <p style="margin:0;">
                Hoy te dejamos los packs a mitad de precio para ti y tu familia, solo por este correo.
              </p>
            </td>
          </tr>

          <!-- Promotions -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <p style="margin:0 0 12px 0; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">PROMOCIONES</p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
                <tr style="background-color:#e6f2f1;">
                  <td style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff;">
                    <strong>Pack Favorito</strong> · 3 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">50% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$52.500</strong><br>
                    <span style="font-size:13px; color:#8a97a3; text-decoration:line-through;">$105.000</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #e6f2f1;">
                    <strong>Pack Familiar</strong> · 5 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">50% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #e6f2f1; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$87.500</strong><br>
                    <span style="font-size:13px; color:#8a97a3; text-decoration:line-through;">$175.000</span>
                  </td>
                </tr>
                <tr style="background-color:#e6f2f1;">
                  <td style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff;">
                    <strong>Pack Regala Salud</strong> · 10 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">60% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$140.000</strong><br>
                    <span style="font-size:13px; color:#8a97a3; text-decoration:line-through;">$350.000</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #e6f2f1;">
                    <strong>Promo Believer</strong> · 12 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">60% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #e6f2f1; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$168.000</strong><br>
                    <span style="font-size:13px; color:#8a97a3;">antes <span style="text-decoration:line-through;">$420.000</span></span>
                  </td>
                </tr>
                <tr style="background-color:#e6f2f1;">
                  <td style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff;">
                    <strong>Promo Empresa</strong> · 24 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">70% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; border-bottom:1px solid #ffffff; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$255.000</strong><br>
                    <span style="font-size:13px; color:#8a97a3;">antes <span style="text-decoration:line-through;">$840.000</span></span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 12px; font-size:15px; line-height:22px;">
                    <strong>Promo Eco Hotel</strong> · 24 und<br>
                    <span style="font-size:13px; color:#3a7b78; font-weight:bold;">70% OFF</span>
                  </td>
                  <td align="right" style="padding:12px 12px; font-size:15px; line-height:22px; white-space:nowrap;">
                    <strong style="font-size:18px; color:#2f3e4e;">$255.000</strong><br>
                    <span style="font-size:13px; color:#8a97a3;">antes <span style="text-decoration:line-through;">$840.000</span></span>
                  </td>
                </tr>
              </table>
              <p style="margin:12px 0 0 0; font-size:13px; line-height:20px; color:#6b7883;">
                Envío gratis vía Coordinadora y Envía.
              </p>
            </td>
          </tr>

          <!-- Call to action -->
          <tr>
            <td align="center" style="padding:24px 32px 32px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" style="background-color:#3a7b78; border-radius:6px;">
                    <a href="__STORE_URL__"
                       style="display:inline-block; padding:14px 32px; font-size:16px; font-weight:bold; color:#ffffff; text-decoration:none; border-radius:6px;">
                      Quiero mi 50% OFF
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:12px 0 0 0; font-size:13px; color:#6b7883;">Oferta válida solo por hoy.</p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:18px 32px; background-color:#f2f5f6; font-size:12px; line-height:18px; color:#8a97a3; text-align:center;">
              Recibes este correo porque compraste en Save a Playa.<br>
              Save a Playa · Colombia ·
              <a href="<%asm_group_unsubscribe_raw_url%>" style="color:#8a97a3; text-decoration:underline;">Cancelar suscripción</a>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".replace("__IMAGE_URL__", IMAGE_URL).replace("__STORE_URL__", STORE_URL)


def upgrade():
    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT id FROM email_templates WHERE name = :name"), {"name": TEMPLATE_NAME}
    ).first()
    if exists:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        sa.text(
            "INSERT INTO email_templates (name, subject, html_body, updated_by, created_at, updated_at) "
            "VALUES (:name, :subject, :html_body, :updated_by, :created_at, :updated_at)"
        ),
        {
            "name": TEMPLATE_NAME,
            "subject": TEMPLATE_SUBJECT,
            "html_body": TEMPLATE_HTML,
            "updated_by": "seed",
            "created_at": now,
            "updated_at": now,
        },
    )


def downgrade():
    op.get_bind().execute(
        sa.text("DELETE FROM email_templates WHERE name = :name AND updated_by = 'seed'"),
        {"name": TEMPLATE_NAME},
    )
