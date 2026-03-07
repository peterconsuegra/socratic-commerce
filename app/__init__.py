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

    app.config["DEBUG"] = True
    app.config["ENV"] = "development"

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

    return app