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


class UserAlreadyExists(ValueError):
    pass


class UserNotFound(LookupError):
    pass


class BudgetNotFound(LookupError):
    pass


class GoalNotFound(LookupError):
    pass


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL не настроен")
    if psycopg2 is None:
        if os.getenv("TESTING") == "true":
            return None
        raise RuntimeError("Не установлена зависимость psycopg2-binary")
    return psycopg2.connect(database_url)


@contextmanager
def _transaction():
    connection = get_connection()
    try:
        yield connection
        if connection:
            connection.commit()
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


def _validate_operation(operation_type, amount, category):
    try:
        amount = Decimal(str(amount).replace(" ", "").replace(" ", "").replace(",", "."))
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
            # 1. Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Operations table — create fresh with user_id
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                    date TEXT NOT NULL,
                    operation_type TEXT NOT NULL CHECK (operation_type IN ('Доход', 'Расход')),
                    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
                    category TEXT
                )
            """)

            # 2b. Safe migration: add user_id column if it's missing from an
            # existing pre-v2 database (does nothing on fresh installs)
            cursor.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{TABLE_NAME}' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE {TABLE_NAME} ADD COLUMN user_id BIGINT;
                    END IF;
                END $$;
            """)

            # 3. Budgets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
                    period TEXT DEFAULT 'month',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. Goals table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    target_amount NUMERIC(14, 2) NOT NULL CHECK (target_amount > 0),
                    current_amount NUMERIC(14, 2) DEFAULT 0 CHECK (current_amount >= 0),
                    deadline TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. User settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    language TEXT DEFAULT 'RU',
                    theme TEXT DEFAULT 'light',
                    currency TEXT DEFAULT 'KZT'
                )
            """)


# User operations
def create_user(email: str, password_hash: str) -> int:
    email = email.strip().lower()
    with _transaction() as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (email, password_hash)
                )
                user_id = cursor.fetchone()[0]
                # Create default settings
                cursor.execute(
                    "INSERT INTO user_settings (user_id) VALUES (%s)",
                    (user_id,)
                )
                return user_id
            except Exception as exc:
                if "unique constraint" in str(exc).lower() or "duplicate key" in str(exc).lower():
                    raise UserAlreadyExists("Пользователь с таким email уже существует") from exc
                raise


def get_user_by_email(email: str):
    email = email.strip().lower()
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email, password_hash, created_at FROM users WHERE email = %s", (email,))
            return cursor.fetchone()
    finally:
        connection.close()


def get_user_by_id(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email, password_hash, created_at FROM users WHERE id = %s", (user_id,))
            return cursor.fetchone()
    finally:
        connection.close()


# User settings
def get_user_settings(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT language, theme, currency FROM user_settings WHERE user_id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
            if not row:
                # Fallback default if not present
                return {"language": "RU", "theme": "light", "currency": "KZT"}
            return {"language": row[0], "theme": row[1], "currency": row[2]}
    finally:
        connection.close()


def update_user_settings(user_id: int, language: str = None, theme: str = None, currency: str = None):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            # Ensure settings exist
            cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))

            updates = []
            params = []
            if language is not None:
                updates.append("language = %s")
                params.append(language)
            if theme is not None:
                updates.append("theme = %s")
                params.append(theme)
            if currency is not None:
                updates.append("currency = %s")
                params.append(currency)

            if updates:
                params.append(user_id)
                cursor.execute(f"UPDATE user_settings SET {', '.join(updates)} WHERE user_id = %s", tuple(params))


def update_user_password(user_id: int, new_password_hash: str):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_password_hash, user_id))


def delete_user_account(user_id: int):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            # Cascading deletes are handled by ON DELETE CASCADE in table definitions
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            if cursor.rowcount == 0:
                raise UserNotFound("Пользователь не найден")


# Operations
def add_operation(user_id: int, date, operation_type, amount, category=None):
    amount, category = _validate_operation(operation_type, amount, category)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE_NAME} (user_id, date, operation_type, amount, category) VALUES (%s, %s, %s, %s, %s)",
                (user_id, date, operation_type, amount, category),
            )


def get_operations(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, date, operation_type, amount, category FROM {TABLE_NAME} WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return cursor.fetchall()
    finally:
        connection.close()


def delete_operation(operation_id: int, user_id: int):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {TABLE_NAME} WHERE id = %s AND user_id = %s",
                (operation_id, user_id)
            )
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")


def update_operation(operation_id: int, user_id: int, date, operation_type, amount, category=None):
    amount, category = _validate_operation(operation_type, amount, category)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {TABLE_NAME}
                    SET date = %s, operation_type = %s, amount = %s, category = %s
                    WHERE id = %s AND user_id = %s""",
                (date, operation_type, amount, category, operation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")


# Budgets
def get_budgets(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, category, amount, period FROM budgets WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return cursor.fetchall()
    finally:
        connection.close()


def add_budget(user_id: int, category: str, amount, period: str = 'month'):
    try:
        amount = Decimal(str(amount).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception as exc:
        raise ValueError("Сумма бюджета должна быть числом") from exc
    if amount <= 0:
        raise ValueError("Сумма бюджета должна быть больше нуля")
    category = category.strip()

    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO budgets (user_id, category, amount, period) VALUES (%s, %s, %s, %s)",
                (user_id, category, amount, period)
            )


def update_budget(budget_id: int, user_id: int, amount):
    try:
        amount = Decimal(str(amount).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception as exc:
        raise ValueError("Сумма бюджета должна быть числом") from exc
    if amount <= 0:
        raise ValueError("Сумма бюджета должна быть больше нуля")

    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE budgets SET amount = %s WHERE id = %s AND user_id = %s",
                (amount, budget_id, user_id)
            )
            if cursor.rowcount == 0:
                raise BudgetNotFound("Бюджет не найден")


def delete_budget(budget_id: int, user_id: int):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM budgets WHERE id = %s AND user_id = %s",
                (budget_id, user_id)
            )
            if cursor.rowcount == 0:
                raise BudgetNotFound("Бюджет не найден")


# Goals
def get_goals(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, title, target_amount, current_amount, deadline FROM goals WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return cursor.fetchall()
    finally:
        connection.close()


def add_goal(user_id: int, title: str, target_amount, current_amount=0, deadline=None):
    try:
        target_amount = Decimal(str(target_amount).replace(" ", "").replace(" ", "").replace(",", "."))
        current_amount = Decimal(str(current_amount).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception as exc:
        raise ValueError("Суммы цели должны быть числами") from exc
    if target_amount <= 0:
        raise ValueError("Целевая сумма должна быть больше нуля")
    title = title.strip()

    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO goals (user_id, title, target_amount, current_amount, deadline) VALUES (%s, %s, %s, %s, %s)",
                (user_id, title, target_amount, current_amount, deadline)
            )


def update_goal_progress(goal_id: int, user_id: int, current_amount):
    try:
        current_amount = Decimal(str(current_amount).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception as exc:
        raise ValueError("Сумма должна быть числом") from exc
    if current_amount < 0:
        raise ValueError("Накопленная сумма не может быть отрицательной")

    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE goals SET current_amount = %s WHERE id = %s AND user_id = %s",
                (current_amount, goal_id, user_id)
            )
            if cursor.rowcount == 0:
                raise GoalNotFound("Цель не найдена")


def delete_goal(goal_id: int, user_id: int):
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM goals WHERE id = %s AND user_id = %s",
                (goal_id, user_id)
            )
            if cursor.rowcount == 0:
                raise GoalNotFound("Цель не найдена")
