"""point email template unsubscribe links at the app's own page

Revision ID: f3c7a9e1b5d2
Revises: e8b2d6f4a1c7
Create Date: 2026-09-30

The seeded template's footer linked to <%asm_group_unsubscribe_raw_url%>,
which SendGrid fills only when an unsubscribe group is set in Settings;
without one the tag went out as literal text and the link was dead. The
{{unsubscribe_url}} placeholder is each recipient's page in this app, which
also flips their email_subscriptions switch.

Only that tag is replaced, so it is safe on templates edited in the UI, and
on clones of the seed that carry the same footer.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "f3c7a9e1b5d2"
down_revision = "e8b2d6f4a1c7"
branch_labels = None
depends_on = None

ASM_TAG = "<%asm_group_unsubscribe_raw_url%>"
APP_TAG = "{{unsubscribe_url}}"


def _swap(old: str, new: str):
    conn = op.get_bind()
    # Matched in Python, not with LIKE: "%" and "_" in the tags are LIKE wildcards.
    rows = conn.execute(sa.text("SELECT id, name, html_body FROM email_templates")).fetchall()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        if old not in (row.html_body or ""):
            continue
        conn.execute(
            sa.text("UPDATE email_templates SET html_body = :html, updated_at = :now WHERE id = :id"),
            {"html": row.html_body.replace(old, new), "now": now, "id": row.id},
        )
        print(f"email template {row.name!r}: unsubscribe link {old} -> {new}")


def upgrade():
    _swap(ASM_TAG, APP_TAG)


def downgrade():
    _swap(APP_TAG, ASM_TAG)
