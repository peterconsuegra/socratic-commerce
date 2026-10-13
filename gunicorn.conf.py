# gunicorn.conf.py - read by gunicorn when it starts the web service (the
# start command's flags take precedence over settings here; hooks live here).
"""Gunicorn hooks for the web service."""


def post_worker_init(worker):
    """Start syncing the orders as soon as a worker is up, if they are missing or stale."""
    try:
        from app.routes.common import start_orders_sync_if_needed

        start_orders_sync_if_needed(worker.wsgi)
    except Exception:
        worker.log.exception("Could not start the orders sync at worker start; requests will start it")
