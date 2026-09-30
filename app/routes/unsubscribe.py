# app/routes/unsubscribe.py
"""
The customer-facing unsubscribe page every email links to.

Public on purpose: a customer arriving from their inbox has no session. The
random token in the URL is the credential (see models.EmailSubscription), so
the page never asks for the address and the URL never contains it.

GET only shows the page; the switch flips on POST. The page's button posts,
and so does a mailbox's own Unsubscribe button (RFC 8058: the body is
"List-Unsubscribe=One-Click"). Security scanners and link prefetchers follow
links with GET, so a GET that unsubscribed would opt out customers who
never clicked.
"""
import logging

from flask import make_response, render_template, request

from app.models import EmailSubscription
from app.services.email_subscriptions import set_unsubscribed, subscription_by_token, tokens_for

from . import main
from .common import external_url

logger = logging.getLogger(__name__)

STORE_URL = "https://saveaplaya.org"


def unsubscribe_urls(emails) -> dict[str, str]:
    """{lowercased email: absolute unsubscribe URL}, creating tokens as needed."""
    return {email: external_url("main.email_unsubscribe", token=token)
            for email, token in tokens_for(emails).items()}


def _masked(email: str) -> str:
    """ma•••@gmail.com: enough for the customer to recognise their address,
    not enough to hand it to whoever the email was forwarded to."""
    local, _, domain = email.partition("@")
    return f"{local[:2]}•••@{domain}"


def _page(state: str, sub: EmailSubscription | None = None, status: int = 200):
    resp = make_response(render_template(
        "unsubscribe.html",
        state=state,
        token=sub.token if sub else "",
        masked_email=_masked(sub.email) if sub else "",
        store_url=STORE_URL,
    ), status)
    # The token is a credential: keep the page out of caches and search
    # engines, and out of the Referer when the customer follows the store link.
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@main.route("/unsubscribe/<token>", methods=["GET", "POST"])
def email_unsubscribe(token):
    """GET: the confirm page. POST: turn the customer's switch on (unsubscribed)."""
    sub = subscription_by_token(token)
    if sub is None:
        return _page("invalid", status=404)
    if request.method == "GET":
        return _page("unsubscribed" if sub.unsubscribed else "confirm", sub)

    one_click = request.form.get("List-Unsubscribe") == "One-Click"
    source = EmailSubscription.SOURCE_ONE_CLICK if one_click else EmailSubscription.SOURCE_PAGE
    if set_unsubscribed(sub, True, source):
        logger.info("email subscription %d unsubscribed (%s)", sub.id, source)
    return _page("done", sub)


@main.route("/unsubscribe/<token>/resubscribe", methods=["POST"])
def email_resubscribe(token):
    """Undo, for a customer who unsubscribed by mistake."""
    sub = subscription_by_token(token)
    if sub is None:
        return _page("invalid", status=404)
    if set_unsubscribed(sub, False):
        logger.info("email subscription %d resubscribed", sub.id)
    return _page("resubscribed", sub)
