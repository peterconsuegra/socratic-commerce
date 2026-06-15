import click
from flask import current_app
from app import db
from app.models import ApiToken, User


def register_cli(app):
    @app.cli.command("make-admin")
    @click.argument("email")
    def make_admin(email):
        """Assign admin role to a user by email (username field)."""
        email = (email or "").strip().lower()

        user = User.query.filter_by(username=email).first()
        if not user:
            raise click.ClickException(f"User not found: {email}")

        user.role = "admin"
        db.session.commit()

        click.echo(f"OK: {user.username} is now role=admin")

    @app.cli.command("create-api-token")
    @click.argument("name")
    def create_api_token(name):
        """Create an API token (for ROAS Link / MCP) and print it once."""
        name = (name or "").strip()
        if not name:
            raise click.ClickException("Token name is required.")

        token, raw = ApiToken.generate(name)
        db.session.add(token)
        db.session.commit()

        click.echo(f"OK: created API token '{name}'")
        click.echo("Copy it now — it will not be shown again:")
        click.echo(raw)

    @app.cli.command("list-api-tokens")
    def list_api_tokens():
        """List API tokens (prefixes only; raw tokens are never stored)."""
        tokens = ApiToken.query.order_by(ApiToken.id.desc()).all()
        if not tokens:
            click.echo("No API tokens.")
            return
        for t in tokens:
            status = "revoked" if t.revoked else "active"
            click.echo(f"#{t.id}  {t.token_prefix}…  [{status}]  {t.name}")
