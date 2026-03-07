import sqlite3

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row  # Enable named column access
    return conn

def init_db():
    """
    Initialize the database and ensure the 'options' table exists.
    """
    conn = get_db_connection()
    with conn:
        # Ensure the 'options' table exists
        conn.execute('''
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_key TEXT NOT NULL UNIQUE,
                meta_value TEXT
            )
        ''')
    conn.close()

def ensure_required_fields():
    """
    Ensure that the 'orders_url' and 'plugin_key' entries exist in the 'options' table.
    If they do not exist, add them with a default value.
    """
    conn = get_db_connection()
    with conn:
        # Define the fields and their default values
        required_fields = {
            'orders_url': 'https://example.com/wp-json/wc-last-orders-json/v1/orders',
            'api_key': 'xxxxxx'
        }

        # Check for each field and insert if missing
        for key, value in required_fields.items():
            result = conn.execute('SELECT COUNT(*) FROM options WHERE meta_key = ?', (key,)).fetchone()
            if result[0] == 0:  # Field does not exist
                conn.execute(
                    'INSERT INTO options (meta_key, meta_value) VALUES (?, ?)',
                    (key, value)
                )
    conn.close()
