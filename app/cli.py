import click
from flask import current_app
from app import db
from app.models import User


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
