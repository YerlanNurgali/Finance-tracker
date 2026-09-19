import os

import psycopg2
from dotenv import load_dotenv


load_dotenv()

TABLE_NAME = "operations"


def get_connection():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError("DATABASE_URL не настроен")

    return psycopg2.connect(database_url)


def create_database():
    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT
            )
        """)

        connection.commit()
    finally:
        connection.close()


def add_operation(date, operation_type, amount, category=None):
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")

    if operation_type not in ("Доход", "Расход"):
        raise ValueError("Недопустимый тип операции")

    if operation_type == "Расход" and not category:
        raise ValueError("Для расхода нужна категория")

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            f"""
            INSERT INTO {TABLE_NAME}
            (date, operation_type, amount, category)
            VALUES (%s, %s, %s, %s)
            """,
            (date, operation_type, amount, category)
        )

        connection.commit()
    finally:
        connection.close()


def get_operations():
    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            f"""
            SELECT id, date, operation_type, amount, category
            FROM {TABLE_NAME}
            ORDER BY id
            """
        )

        operations = cursor.fetchall()

        return operations
    finally:
        connection.close()


def delete_operation(operation_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            f"DELETE FROM {TABLE_NAME} WHERE id = %s",
            (operation_id,)
        )

        connection.commit()
    finally:
        connection.close()


def update_operation(operation_id, date, operation_type, amount, category=None):
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")

    if operation_type not in ("Доход", "Расход"):
        raise ValueError("Недопустимый тип операции")

    if operation_type == "Расход" and not category:
        raise ValueError("Для расхода нужна категория")

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET date = %s,
                operation_type = %s,
                amount = %s,
                category = %s
            WHERE id = %s
            """,
            (date, operation_type, amount, category, operation_id)
        )

        connection.commit()
    finally:
        connection.close()
