# app/routes/female_sales.py
"""
/female_sales_monthly: the root view (/, Monthly Sales) for the orders the
store marks female (gender "female"; "unknown" is never counted, the app does
not guess gender). Every purchase counts, first or repeat. Rendered by
render_monthly_by_kind (routes/monthly_by_kind.py).
"""
from flask_login import login_required

from . import main
from .monthly_by_kind import render_monthly_by_kind


@main.route("/female_sales_monthly")
@login_required
def female_sales_monthly():
    return render_monthly_by_kind("female")
