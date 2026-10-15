"""seed the two "Pack Emprendedor" email templates

Revision ID: c3f8a1d6e9b4
Revises: b4e9d2a7c1f3
Create Date: 2026-10-15

Same approach as e5a2b8d4c7f1: a data migration inserts the template once, by
name, so it lands on the deployed app and is edited in the UI from then on.
It promotes https://saveaplaya.org/product/promocion-emprendedor/ (12 units in
individual boxes for $210.000, resold at $35.000 each) to past buyers, with
a discount coupon so the first pack leaves even more profit.

Two angles on the same email, to test against each other: "¿Y si Save a
Playa te paga a ti?" and "¿Y si Save a Playa te lleva al mar?", which plays
on the coupon's name. The second is the first with a few passages swapped;
each has its own utm_campaign so their orders are told apart in the reports.

The product photos on saveaplaya.org are WebP, which Outlook does not render,
so the hero is the template's own uploaded image ({{hero_image_url}}): sends
stay blocked until a JPG or PNG is uploaded in the editor. Sections are
delimited by HTML comments so a later migration can swap one without
touching edits made in the UI.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "c3f8a1d6e9b4"
down_revision = "b4e9d2a7c1f3"
branch_labels = None
depends_on = None

PRODUCT_URL = "https://saveaplaya.org/product/promocion-emprendedor/"
UTM = "utm_source=email&utm_medium=sendgrid&utm_campaign={campaign}"
WHATSAPP_URL = ("https://wa.me/573123847253?text=Hola%2C%20quiero%20saber%20m%C3%A1s%20"
                "de%20la%20Promoci%C3%B3n%20Emprendedor")

# The campaign's WooCommerce coupon: 10% on top of the pack's sale price
# ($210.000 -> $189.000). The email says it is for this week only and names
# no date; the coupon itself expires in WooCommerce on 2026-10-31.
COUPON_CODE = "voy-al-caribe"

# Units 1-6 pay the pack back, 7-12 are profit.
_UNIT_CELL = ('<td align="center" width="8%" style="height:34px; font-size:14px; font-weight:bold; '
              'border-radius:4px; background-color:{bg}; color:{fg};">{n}</td>')
UNITS_ROW = "\n                  ".join(
    _UNIT_CELL.format(n=n, bg="#bfdcda" if n <= 6 else "#3a7b78", fg="#2f3e4e" if n <= 6 else "#ffffff")
    for n in range(1, 13)
)

_BASE_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Promoción Emprendedor: 12 desodorantes naturales para vender</title>
</head>
<body style="margin:0; padding:0; background-color:#f2f5f6; font-family:Arial, Helvetica, sans-serif; color:#2f3e4e;">
  <!-- Preheader: shown next to the subject in the inbox, hidden in the body -->
  <div style="display:none; max-height:0; overflow:hidden; font-size:1px; line-height:1px; color:#f2f5f6;">
    Con tu cupón, 12 desodorantes por $189.000 para vender a $35.000 c/u. Solo por esta semana.
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
              <a href="__PRODUCT_URL__?__UTM__&utm_content=hero" style="text-decoration:none;">
                <img src="{{hero_image_url}}" width="600" alt="Promoción Emprendedor: 12 desodorantes naturales Save a Playa en caja individual"
                     style="display:block; width:100%; max-width:600px; height:auto; border:0;">
              </a>
            </td>
          </tr>

          <!-- Greeting -->
          <tr>
            <td style="padding:28px 32px 8px 32px;">
              <p style="margin:0 0 8px 0; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">PROMOCIÓN EMPRENDEDOR</p>
              <h1 style="margin:0; font-size:24px; line-height:31px; color:#2f3e4e;">
                ¡Hola, {{name}}! ¿Y si Save a Playa te paga a ti? 💼
              </h1>
            </td>
          </tr>

          <!-- Body copy -->
          <tr>
            <td style="padding:12px 32px 8px 32px; font-size:16px; line-height:25px; color:#2f3e4e;">
              <p style="margin:0 0 14px 0;">
                Tú ya sabes que funciona. Ahora puedes convertirlo en un negocio: te dejamos
                <strong>12 desodorantes naturales a $15.750 cada uno</strong>, armados en su caja
                individual y listos para vender.
              </p>
              <p style="margin:0;">
                Ideal si ya vendes por catálogo o si quieres empezar tu propia línea de productos
                amigables con el medio ambiente.
              </p>
            </td>
          </tr>

          <!-- Offer -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#e6f2f1; border-radius:8px;">
                <tr>
                  <td align="center" style="padding:22px 20px 6px 20px; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">
                    12 UNIDADES EN CAJA INDIVIDUAL
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 4px 20px; font-size:30px; line-height:38px; color:#2f3e4e;">
                    <strong>$189.000</strong>
                    <span style="font-size:17px; color:#8a97a3; text-decoration:line-through; white-space:nowrap;">$420.000</span>
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 6px 20px; font-size:17px; line-height:25px; font-weight:bold; color:#3a7b78;">
                    $15.750 por unidad · envío gratis
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 20px 22px 20px; font-size:13px; line-height:20px; color:#6b7883;">
                    Precio final con tu cupón <strong>__COUPON_CODE__</strong>: 50% OFF + 10% adicional.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- The numbers -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <p style="margin:0 0 12px 0; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">ASÍ DE SIMPLES SON LAS CUENTAS</p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse; font-size:15px; line-height:22px; color:#2f3e4e;">
                <tr>
                  <td style="padding:10px 12px; border-bottom:1px solid #e6f2f1;">Pagas por cada desodorante</td>
                  <td align="right" style="padding:10px 12px; border-bottom:1px solid #e6f2f1; white-space:nowrap; font-weight:bold;">$15.750</td>
                </tr>
                <tr>
                  <td style="padding:10px 12px; border-bottom:1px solid #e6f2f1;">Lo vendes por catálogo a</td>
                  <td align="right" style="padding:10px 12px; border-bottom:1px solid #e6f2f1; white-space:nowrap; font-weight:bold;">$35.000</td>
                </tr>
                <tr>
                  <td style="padding:10px 12px; border-bottom:1px solid #e6f2f1;">Inviertes en las 12 unidades</td>
                  <td align="right" style="padding:10px 12px; border-bottom:1px solid #e6f2f1; white-space:nowrap; font-weight:bold;">$189.000</td>
                </tr>
                <tr>
                  <td style="padding:10px 12px; border-bottom:1px solid #e6f2f1;">Recibes al venderlas todas</td>
                  <td align="right" style="padding:10px 12px; border-bottom:1px solid #e6f2f1; white-space:nowrap; font-weight:bold;">$420.000</td>
                </tr>
                <tr style="background-color:#3a7b78;">
                  <td style="padding:12px 12px; color:#ffffff; font-weight:bold;">
                    Tu ganancia<br>
                    <span style="font-size:13px; font-weight:normal;">el 122% de tu inversión</span>
                  </td>
                  <td align="right" style="padding:12px 12px; color:#ffffff; white-space:nowrap; font-size:20px; font-weight:bold;">$231.000</td>
                </tr>
              </table>
              <p style="margin:12px 0 0 0; font-size:13px; line-height:20px; color:#6b7883;">
                Tu cliente paga lo mismo que en nuestra tienda en línea.
              </p>
            </td>
          </tr>

          <!-- Payback -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <p style="margin:0 0 12px 0; font-size:18px; line-height:26px; font-weight:bold; color:#2f3e4e;">
                Con la venta número 6, el pack ya está pagado.
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="3" border="0">
                <tr>
                  __UNITS_ROW__
                </tr>
              </table>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px; font-size:13px; line-height:19px; color:#6b7883;">
                <tr>
                  <td width="50%" valign="top" style="padding:0 4px 0 3px;">
                    <span style="color:#3a7b78; font-weight:bold;">1 a 6:</span> recuperas tu inversión
                  </td>
                  <td width="50%" valign="top" align="right" style="padding:0 3px 0 4px;">
                    <span style="color:#3a7b78; font-weight:bold;">7 a 12:</span> son tu ganancia
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Coupon and how to redeem it -->
          <tr>
            <td style="padding:28px 32px 8px 32px;">
              <p style="margin:0 0 6px 0; font-size:18px; line-height:26px; font-weight:bold; color:#2f3e4e;">
                Y para que ganes aún más desde el inicio 🎁
              </p>
              <p style="margin:0 0 16px 0; font-size:16px; line-height:25px; color:#2f3e4e;">
                Te damos un <strong>10% adicional</strong> sobre la Promoción Emprendedor: con este cupón
                pagas <strong>$189.000</strong> en lugar de $210.000. Escríbelo en el checkout para que se aplique.
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:2px dashed #3a7b78; border-radius:8px; background-color:#f7fbfa;">
                <tr>
                  <td align="center" style="padding:20px 12px 6px 12px; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">
                    TU CUPÓN · 10% ADICIONAL
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 12px 6px 12px; font-family:'Courier New', Courier, monospace; font-size:24px; line-height:30px; font-weight:bold; letter-spacing:1px; color:#2f3e4e;">
                    __COUPON_CODE__
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 12px 20px 12px; font-size:13px; line-height:19px; color:#6b7883;">
                    Válido solo por esta semana.
                  </td>
                </tr>
              </table>

              <p style="margin:20px 0 8px 0; font-size:15px; line-height:22px; font-weight:bold; color:#2f3e4e;">
                Cómo usarlo
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:15px; line-height:23px; color:#2f3e4e;">
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">1.</td>
                  <td style="padding:4px 0;">
                    Agrega la <a href="__PRODUCT_URL__?__UTM__&utm_content=steps" style="color:#3a7b78; font-weight:bold;">Promoción Emprendedor</a>
                    a tu carrito en saveaplaya.org.
                  </td>
                </tr>
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">2.</td>
                  <td style="padding:4px 0;">
                    En el checkout, a la hora de hacer el pago, haz clic en
                    <strong>“Haz clic aquí para introducir tu código”</strong>.
                  </td>
                </tr>
                <tr>
                  <td width="28" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">3.</td>
                  <td style="padding:4px 0;">
                    Escribe <strong>__COUPON_CODE__</strong>, pulsa <strong>Aplicar cupón</strong> y verás el
                    descuento antes de confirmar tu pago.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Why it sells -->
          <tr>
            <td style="padding:24px 32px 8px 32px;">
              <p style="margin:0 0 10px 0; font-size:13px; letter-spacing:2px; color:#3a7b78; font-weight:bold;">UN PRODUCTO QUE SE RECOMIENDA SOLO</p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:15px; line-height:23px; color:#2f3e4e;">
                <tr>
                  <td width="26" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">✓</td>
                  <td style="padding:4px 0;"><strong>Sin aluminio ni parabenos.</strong> Regula el sudor sin tapar los poros.</td>
                </tr>
                <tr>
                  <td width="26" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">✓</td>
                  <td style="padding:4px 0;"><strong>+48 h de protección</strong> con plata antibacterial que combate el olor en lugar de taparlo.</td>
                </tr>
                <tr>
                  <td width="26" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">✓</td>
                  <td style="padding:4px 0;"><strong>Rinde más de 30 días:</strong> un tubo de 20 ml dura más de un mes.</td>
                </tr>
                <tr>
                  <td width="26" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">✓</td>
                  <td style="padding:4px 0;"><strong>Vegano, unisex</strong> y en empaque biodegradable. INVIMA NSOC08736-21CO.</td>
                </tr>
                <tr>
                  <td width="26" valign="top" style="padding:4px 0; font-weight:bold; color:#3a7b78;">✓</td>
                  <td style="padding:4px 0;"><strong>Más de 200.000 unidades vendidas</strong> y más de 2.000 testimonios positivos.</td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Call to action -->
          <tr>
            <td align="center" style="padding:28px 32px 8px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" style="background-color:#3a7b78; border-radius:6px;">
                    <a href="__PRODUCT_URL__?__UTM__&utm_content=cta"
                       style="display:inline-block; padding:14px 32px; font-size:16px; font-weight:bold; color:#ffffff; text-decoration:none; border-radius:6px;">
                      Quiero mi Pack Emprendedor
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:12px 0 0 0; font-size:14px; line-height:21px; color:#2f3e4e;">
                No olvides tu cupón <strong>__COUPON_CODE__</strong> en el checkout:<br>
                tu pack queda en <strong>$189.000</strong> solo por esta semana.
              </p>
              <p style="margin:6px 0 0 0; font-size:13px; line-height:20px; color:#6b7883;">
                Envío gratis a toda Colombia en menos de 72 horas.<br>
                Paga en línea, por transferencia o con Llave.
              </p>
            </td>
          </tr>

          <!-- Guarantee and WhatsApp -->
          <tr>
            <td style="padding:20px 32px 28px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:8px; background-color:#e6f2f1;">
                <tr>
                  <td align="center" style="padding:16px 18px; font-size:15px; line-height:23px; color:#2f3e4e;">
                    <strong>Garantía Cero Estrés:</strong> si no te funciona, te devolvemos tu dinero.<br>
                    <span style="font-size:14px;">¿Tienes dudas o quieres más de un pack?
                      <a href="__WHATSAPP_URL__" style="color:#3a7b78; font-weight:bold;">Escríbenos por WhatsApp</a></span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Mission -->
          <tr>
            <td align="center" style="padding:0 32px 28px 32px; font-size:14px; line-height:21px; color:#6b7883;">
              <strong style="color:#3a7b78;">Salva tu piel, salva la playa 🌊</strong><br>
              Con cada compra apoyas proyectos de limpieza y activismo para devolverle al mar su magia.
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:18px 32px; background-color:#f2f5f6; font-size:12px; line-height:18px; color:#8a97a3; text-align:center;">
              Recibes este correo porque compraste en Save a Playa.<br>
              Save a Playa · Colombia ·
              <a href="{{unsubscribe_url}}" style="color:#8a97a3; text-decoration:underline;">Cancelar suscripción</a>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _fill(html: str, campaign: str) -> str:
    return (html.replace("__PRODUCT_URL__", PRODUCT_URL)
            .replace("__UTM__", UTM.format(campaign=campaign))
            .replace("__WHATSAPP_URL__", WHATSAPP_URL)
            .replace("__UNITS_ROW__", UNITS_ROW)
            .replace("__COUPON_CODE__", COUPON_CODE))


def _swapped(html: str, swaps: list[tuple[str, str]]) -> str:
    for old, new in swaps:
        assert html.count(old) == 1, f"expected exactly one {old!r} in the base template"
        html = html.replace(old, new)
    return html


# "¿Y si Save a Playa te lleva al mar?": the base email with the greeting,
# intro, payback, coupon and mission passages retold as saving for a trip.
SEA_SWAPS = [
    ("<title>Promoción Emprendedor: 12 desodorantes naturales para vender</title>",
     "<title>¿Y si Save a Playa te lleva al mar?</title>"),
    ("Con tu cupón, 12 desodorantes por $189.000 para vender a $35.000 c/u. Solo por esta semana.",
     "Vende 12 desodorantes naturales y con tu ganancia empieza a pagar tu viaje al Caribe. "
     "Cupón solo por esta semana."),
    ("¡Hola, {{name}}! ¿Y si Save a Playa te paga a ti? 💼",
     "¡Hola, {{name}}! ¿Y si Save a Playa te lleva al mar? 🌊"),
    ("""                Tú ya sabes que funciona. Ahora puedes convertirlo en un negocio: te dejamos
                <strong>12 desodorantes naturales a $15.750 cada uno</strong>, armados en su caja
                individual y listos para vender.
              </p>
              <p style="margin:0;">
                Ideal si ya vendes por catálogo o si quieres empezar tu propia línea de productos
                amigables con el medio ambiente.""",
     """                Tú ya sabes que funciona. Ahora puede llevarte a la playa: te dejamos
                <strong>12 desodorantes naturales a $15.750 cada uno</strong>, armados en su caja
                individual y listos para vender. Lo que ganes es para tu próximo viaje al mar.
              </p>
              <p style="margin:0;">
                Véndelos a tus amigos, a tu familia o a tus clientes de catálogo: cada venta te
                acerca un poco más al Caribe."""),
    ("Con la venta número 6, el pack ya está pagado.",
     "Con la venta número 6 recuperas tu inversión. Las otras 6 te llevan al mar 🏝️"),
    ("</span> son tu ganancia", "</span> tu fondo para el mar"),
    ("Y para que ganes aún más desde el inicio 🎁",
     "Tu cupón se llama __COUPON_CODE__ por algo ✈️"),
    ("""                Te damos un <strong>10% adicional</strong> sobre la Promoción Emprendedor: con este cupón
                pagas <strong>$189.000</strong> en lugar de $210.000. Escríbelo en el checkout para que se aplique.""",
     """                Te damos un <strong>10% adicional</strong> para que llegues al mar más rápido: con este
                cupón pagas <strong>$189.000</strong> en lugar de $210.000. Escríbelo en el checkout para que
                se aplique."""),
    ("Tu ganancia<br>", "Tu fondo para el mar<br>"),
    ("Con cada compra apoyas proyectos de limpieza y activismo para devolverle al mar su magia.",
     "Y mientras vendes, cuidas el mar al que vas a ir: con cada compra apoyas la limpieza de las "
     "playas del Caribe."),
]

TEMPLATES = [
    {
        "name": "Pack Emprendedor · Te paga a ti",
        "subject": "{{name}}, vende Save a Playa y duplica tu inversión 💼🌊",
        "html_body": _fill(_BASE_HTML, "pack_emprendedor_paga"),
    },
    {
        "name": "Pack Emprendedor · Te lleva al mar",
        "subject": "{{name}}, ¿y si Save a Playa te lleva al mar? 🌊",
        "html_body": _fill(_swapped(_BASE_HTML, SEA_SWAPS), "pack_emprendedor_mar"),
    },
]


def upgrade():
    conn = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for template in TEMPLATES:
        exists = conn.execute(
            sa.text("SELECT id FROM email_templates WHERE name = :name"), {"name": template["name"]}
        ).first()
        if exists:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO email_templates (name, subject, html_body, updated_by, created_at, updated_at) "
                "VALUES (:name, :subject, :html_body, :updated_by, :created_at, :updated_at)"
            ),
            {**template, "updated_by": "seed", "created_at": now, "updated_at": now},
        )


def downgrade():
    conn = op.get_bind()
    for template in TEMPLATES:
        conn.execute(
            sa.text("DELETE FROM email_templates WHERE name = :name AND updated_by = 'seed'"),
            {"name": template["name"]},
        )
