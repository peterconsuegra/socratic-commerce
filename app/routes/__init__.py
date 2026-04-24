# app/routes/__init__.py
from flask import Blueprint

main = Blueprint(
    "main",
    __name__,
    template_folder="../templates",
)

# Import route modules so their @main.route decorators register
from . import errors  # noqa: E402,F401
from . import data  # noqa: E402,F401
from . import auth  # noqa: E402,F401
from . import admin  # noqa: E402,F401
from . import monthly  # noqa: E402,F401
from . import daily  # noqa: E402,F401
from . import top  # noqa: E402,F401
from . import insights  # noqa: E402,F401
from . import rankings  # noqa: E402,F401
from . import options  # noqa: E402,F401