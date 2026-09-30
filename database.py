import os
from contextlib import contextmanager
from decimal import Decimal

try:
    import psycopg2
except ImportError:
    psycopg2 = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

load_dotenv()
TABLE_NAME = "operations"
VALID_OPERATION_TYPES = ("Доход", "Расход")


class OperationNotFound(LookupError):
    pass


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL не настроен")
    if psycopg2 is None:
        raise RuntimeError("Не установлена зависимость psycopg2-binary")
    return psycopg2.connect(database_url)


@contextmanager
def _transaction():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _validate_operation(operation_type, amount, category):
    try:
        amount = Decimal(str(amount).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except Exception as exc:
        raise ValueError("Сумма должна быть числом") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    if operation_type not in VALID_OPERATION_TYPES:
        raise ValueError("Недопустимый тип операции")
    category = category.strip() if isinstance(category, str) else category
    if operation_type == "Расход" and not category:
        raise ValueError("Для расхода нужна категория")
    return amount, category


def create_database():
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,
                    date TEXT NOT NULL,
                    operation_type TEXT NOT NULL CHECK (operation_type IN ('Доход', 'Расход')),
                    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
                    category TEXT
                )
            """)
            # Safe in-place upgrades for installations created with the old REAL schema.
            cursor.execute(f"ALTER TABLE {TABLE_NAME} ALTER COLUMN amount TYPE NUMERIC(14, 2) USING amount::numeric")
            cursor.execute(f"""DO $$ BEGIN
                ALTER TABLE {TABLE_NAME} ADD CONSTRAINT operations_type_check
                CHECK (operation_type IN ('Доход', 'Расход'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;""")
            cursor.execute(f"""DO $$ BEGIN
                ALTER TABLE {TABLE_NAME} ADD CONSTRAINT operations_amount_check CHECK (amount > 0);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;""")


def add_operation(date, operation_type, amount, category=None):
    amount, category = _validate_operation(operation_type, amount, category)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE_NAME} (date, operation_type, amount, category) VALUES (%s, %s, %s, %s)",
                (date, operation_type, amount, category),
            )


def get_operations():
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT id, date, operation_type, amount, category FROM {TABLE_NAME} ORDER BY id")
            return cursor.fetchall()
    finally:
        connection.close()


def delete_operation(operation_id):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE id = %s", (operation_id,))
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")


def update_operation(operation_id, date, operation_type, amount, category=None):
    amount, category = _validate_operation(operation_type, amount, category)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {TABLE_NAME}
                    SET date = %s, operation_type = %s, amount = %s, category = %s
                    WHERE id = %s""",
                (date, operation_type, amount, category, operation_id),
            )
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")
