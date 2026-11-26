# app/routes/male_sales.py
"""
/male_sales_monthly: the root view (/, Monthly Sales) for the orders the store
marks male (gender "male"; "unknown" is never counted, the app does not guess
gender). Every purchase counts, first or repeat. Rendered by
render_monthly_by_kind (routes/monthly_by_kind.py).
"""
from flask_login import login_required

from . import main
from .monthly_by_kind import render_monthly_by_kind


@main.route("/male_sales_monthly")
@login_required
def male_sales_monthly():
    return render_monthly_by_kind("male")
