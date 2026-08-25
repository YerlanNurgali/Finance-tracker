import sqlite3


DB_NAME = "finance_tracker.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def create_database():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT
        )
    """)

    connection.commit()
    connection.close()
