# app/routes/inventory.py
"""
/inventory: how many tubes of deodorant were sold in a date range, from the
packs in each order (services/inventory.py).

The range comes from the shared date selector, which fetches the orders into
the page's own file (data/inventory_orders.csv) through /get_data and saves
the dates as options, like /sales_by_location.
"""
import logging
import os

from flask import render_template
from flask_login import login_required

from app.services.get_data import OutdatedOrdersFile
from app.services.inventory import build_inventory
from app.services.periods import range_includes_today

from . import main
from .daily import _saved_date_range

logger = logging.getLogger(__name__)

INVENTORY_FILE = "inventory_orders.csv"


@main.route("/inventory")
@login_required
def inventory():
    """Tubes sold in the saved date range, per pack."""
    date_range, start_date, end_date = _saved_date_range(INVENTORY_FILE)
    path = os.path.join("data", INVENTORY_FILE)

    inventory_stats, error = None, None
    if os.path.exists(path):
        try:
            inventory_stats = build_inventory(path, include_today=range_includes_today(end_date))
        except OutdatedOrdersFile as exc:
            error = str(exc)
        except Exception as exc:
            logger.exception("Failed building the inventory for %s", path)
            error = str(exc)

    return render_template(
        "inventory.html",
        file_name=INVENTORY_FILE,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        has_orders_file=os.path.exists(path),
        inventory=inventory_stats,
        error=error,
    )
