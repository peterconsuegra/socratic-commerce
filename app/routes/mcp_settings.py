# app/routes/mcp_settings.py
"""
/mcp-settings: how to connect Claude to Second Order's MCP server (/mcp,
routes/mcp.py), for admins.

The page shows the server's address and its tools, creates a token for a
connector (an ApiToken named "MCP · <name>", so it is listed with the others
on /admin/api_tokens and revoked the same way), and gives the connector URL
and the Claude Code command with that token in them. The token is shown once,
in the response to the POST that creates it, never stored in plain text.
"""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.i18n import translate as _
from app.models import ApiToken

from . import main
from .admin import _to_bogota
from .common import admin_required
from .mcp import TOOLS

TOKEN_NAME_PREFIX = "MCP · "
SERVER_NAME = "second-order"


def mcp_url() -> str:
    """The public address of /mcp. Railway ends TLS in front of the app, so trust its forwarded scheme."""
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    return f"{scheme}://{request.host}/mcp"


def _connector_tokens():
    tokens = (ApiToken.query.filter(ApiToken.name.startswith(TOKEN_NAME_PREFIX))
              .order_by(ApiToken.id.desc()).all())
    for t in tokens:
        t.label = t.name[len(TOKEN_NAME_PREFIX):]
        t.created_at_bogota = _to_bogota(t.created_at)
        t.last_used_at_bogota = _to_bogota(t.last_used_at)
    return tokens


def _page(new_token: str | None = None, new_label: str | None = None):
    url = mcp_url()
    return render_template(
        "mcp_settings.html",
        mcp_url=url,
        server_name=SERVER_NAME,
        tools=TOOLS,
        tokens=_connector_tokens(),
        new_token=new_token,
        new_label=new_label,
        default_label=f"Claude · {current_user.username}",
    )


@main.route("/mcp-settings")
@login_required
@admin_required
def mcp_settings():
    return _page()


@main.route("/mcp-settings/token", methods=["POST"])
@login_required
@admin_required
def mcp_settings_new_token():
    label = (request.form.get("name") or "").strip()
    if not label:
        flash(_("Give the connector a name."), "danger")
        return redirect(url_for("main.mcp_settings"))
    token, raw = ApiToken.generate(f"{TOKEN_NAME_PREFIX}{label}"[:120])
    db.session.add(token)
    db.session.commit()
    response = _page(new_token=raw, new_label=label)
    return response, 200, {"Cache-Control": "no-store"}


@main.route("/mcp-settings/token/<int:token_id>/revoke", methods=["POST"])
@login_required
@admin_required
def mcp_settings_revoke_token(token_id):
    token = ApiToken.query.get_or_404(token_id)
    if not token.name.startswith(TOKEN_NAME_PREFIX):
        flash(_("That token is not a Claude connector token; manage it on API Tokens."), "danger")
        return redirect(url_for("main.mcp_settings"))
    token.revoked = True
    db.session.commit()
    flash(_("Connector '{name}' revoked. Claude can no longer use it.").format(
        name=token.name[len(TOKEN_NAME_PREFIX):]), "success")
    return redirect(url_for("main.mcp_settings"))
