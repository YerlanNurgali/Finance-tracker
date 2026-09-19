import sqlite3

from database import add_operation


SQLITE_DB = "finance_tracker.db"


def migrate():
    connection = sqlite3.connect(SQLITE_DB)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT date, operation_type, amount, category
        FROM operations
        ORDER BY id
    """)

    operations = cursor.fetchall()
    connection.close()

    for date, operation_type, amount, category in operations:
        add_operation(
            date,
            operation_type,
            amount,
            category
        )

    print(f"Перенесено операций: {len(operations)}")


if __name__ == "__main__":
    migrate()
