# app/routes/errors.py
import traceback

from flask import render_template

from . import main


@main.app_errorhandler(500)
def internal_error(error):
    return render_template(
        "error.html",
        error=str(error),
        traceback=traceback.format_exc(),
    ), 500