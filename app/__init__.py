from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import os
from dotenv import load_dotenv



db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

# Load environment variables from .env if present
load_dotenv()

# Initialize SQLAlchemy
db = SQLAlchemy()
migrate = Migrate()

def create_app():
    app = Flask(__name__)

    app.config["DEBUG"] = True
    app.config["ENV"] = "development"

    from .helpers import format_cop
    app.jinja_env.filters['cop'] = format_cop
    
    from .helpers import get_value
    app.jinja_env.filters['get_value'] = get_value


    # Ensure the static folder is correctly set
    app.static_folder = 'static'
    
    # Set secret key
    app.secret_key = os.getenv('FLASK_KEY', 'default-secret-key')
    
    # Define the base directory
    basedir = os.path.abspath(os.path.dirname(__file__))
    
    # Construct the absolute path to the database
    db_path = os.path.join(basedir, '..', 'database.db')
    
    # Update the SQLALCHEMY_DATABASE_URI with the correct SQLite URI format
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = "main.login"  # The name of the login route

    # Import and register blueprints
    from .routes import main
    app.register_blueprint(main)

    # Provide user loader function
    from .models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register CLI commands (import here to avoid circular import)
    from .cli import register_cli
    register_cli(app)

    return app
