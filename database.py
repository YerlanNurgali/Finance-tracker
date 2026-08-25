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


def add_operation(date, operation_type, amount, category=None):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO operations (date, operation_type, amount, category)
            VALUES (?, ?, ?, ?)
            """,
            (date, operation_type, amount, category)
        )


def get_operations():
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, date, operation_type, amount, category
            FROM operations
            ORDER BY id
            """
        )

        operations = cursor.fetchall()

    return operations

def delete_operation(operation_id):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM operations WHERE id = ?",
            (operation_id,)
        )

    connection.commit()
    connection.close()

def update_operation(operation_id, date, operation_type, amount, category=None):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE operations
            SET date = ?, operation_type = ?, amount = ?, category = ?
            WHERE id = ?
            """,
            (date, operation_type, amount, category, operation_id)
        )

    connection.commit()
    connection.close()
