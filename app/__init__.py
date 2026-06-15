from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import os
from dotenv import load_dotenv



db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

load_dotenv()

def create_app():
    app = Flask(__name__)

    app.config["DEBUG"] = os.getenv("FLASK_DEBUG", "0") == "1"
    app.config["ENV"] = os.getenv("FLASK_ENV", "production")

    from .helpers import format_cop
    app.jinja_env.filters["cop"] = format_cop

    from .helpers import get_value
    app.jinja_env.filters["get_value"] = get_value

    app.static_folder = "static"
    app.secret_key = os.getenv("FLASK_KEY", "default-secret-key")

    basedir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.abspath(os.path.join(basedir, ".."))
    data_dir = os.path.join(project_root, "data")
    db_path = os.path.join(project_root, "database.db")

    app.config["PROJECT_ROOT"] = project_root
    app.config["DATA_DIR"] = data_dir
    app.config["ALL_ORDERS_CSV"] = os.path.join(data_dir, "all_orders.csv")
    app.config["ALL_ORDERS_CACHE_FILE"] = os.path.join(data_dir, ".all_orders_cache_ts")

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Railway ships `postgres://`; SQLAlchemy 1.4+ requires `postgresql://`.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_path
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    os.makedirs(data_dir, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = "main.login"

    from .routes import main
    app.register_blueprint(main)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from .cli import register_cli
    register_cli(app)

    @app.after_request
    def add_api_cors_headers(response):
        # Allow external integrations (ROAS Link / MCP) to call the JSON API.
        from flask import request
        if request.path.startswith("/api/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return response

    return app