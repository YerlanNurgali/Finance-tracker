import logging
import os
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

try:
    import psycopg2
    import psycopg2.pool
except ImportError:
    psycopg2 = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

load_dotenv()
logger = logging.getLogger(__name__)
TABLE_NAME = "operations"
VALID_OPERATION_TYPES = ("Доход", "Расход")


class InvalidInput(ValueError):
    """Data the user sent is not acceptable. The message is meant for the client;
    any other ValueError (configuration, driver) is an internal error."""


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


def _env_positive_int(name, default):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise ValueError(f"{name} должен быть целым положительным числом, получено: {raw!r}")
    return value


def _env_positive_seconds(name, default):
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not (0 < value < float("inf")):
        raise ValueError(f"{name} должен быть положительным числом секунд, получено: {raw!r}")
    return value


# psycopg2 keeps at most POOL_MIN idle connections and closes the rest on
# return, so POOL_MIN is also how many connections survive between bursts.
POOL_MIN = _env_positive_int("DB_POOL_MIN", 5)
POOL_MAX = _env_positive_int("DB_POOL_MAX", 10)
POOL_TIMEOUT = _env_positive_seconds("DB_POOL_TIMEOUT", 10)
# Applies to every connection the pool opens, so an unreachable database
# fails fast instead of hanging startup or a request.
CONNECT_TIMEOUT = 10
# Neon suspends idle compute and drops its connections, so connections idle
# longer than this are pinged before being handed out.
POOL_IDLE_PING_SECONDS = 30

_pool = None
_pool_lock = threading.Lock()
_last_used = {}


class _PooledConnection:
    """Proxy whose close() returns the connection to the pool instead of closing it."""

    def __init__(self, raw, release):
        self._raw = raw
        self._release = release

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def close(self):
        if self._raw is not None:
            raw, self._raw = self._raw, None
            self._release(raw)


def _get_pool(database_url):
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if POOL_MIN > POOL_MAX:
                    logger.warning(
                        "DB_POOL_MIN (%s) больше DB_POOL_MAX (%s), используется %s",
                        POOL_MIN, POOL_MAX, POOL_MAX,
                    )
                pool = psycopg2.pool.ThreadedConnectionPool(
                    min(POOL_MIN, POOL_MAX), POOL_MAX, database_url,
                    connect_timeout=CONNECT_TIMEOUT, keepalives=1, keepalives_idle=30,
                )
                # ThreadedConnectionPool raises instead of waiting when empty;
                # the semaphore makes callers wait up to POOL_TIMEOUT for a slot.
                # It lives on the pool so late returns after close_pool() release
                # the slot of the pool they came from.
                pool.slots = threading.BoundedSemaphore(POOL_MAX)
                _pool = pool
    return _pool


def _is_alive(connection):
    if time.monotonic() - _last_used.get(id(connection), 0) < POOL_IDLE_PING_SECONDS:
        return True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        connection.rollback()
        return True
    except Exception:
        return False


def _checkin(pool, connection):
    try:
        discard = bool(connection.closed)
        if not discard:
            try:
                connection.rollback()  # end read transactions left open by SELECTs
            except Exception:
                discard = True
        _last_used[id(connection)] = time.monotonic()
        try:
            pool.putconn(connection, close=discard)
        except psycopg2.pool.PoolError:
            # close_pool() ran while this connection was in use.
            connection.close()
        if connection.closed:  # discarded by us or as surplus by the pool
            _last_used.pop(id(connection), None)
    finally:
        pool.slots.release()


def _checkout(pool):
    if not pool.slots.acquire(timeout=POOL_TIMEOUT):
        raise RuntimeError("Пул подключений к базе данных исчерпан")
    try:
        connection = pool.getconn()
        # After Neon suspends, every idle connection is dead, so keep discarding
        # until a live one turns up. At most maxconn idle connections exist, so
        # the last getconn() opens a fresh connection.
        for _ in range(pool.maxconn):
            if not connection.closed and _is_alive(connection):
                break
            _last_used.pop(id(connection), None)
            pool.putconn(connection, close=True)
            connection = pool.getconn()
    except Exception:
        pool.slots.release()
        raise
    return _PooledConnection(connection, lambda raw: _checkin(pool, raw))


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            _last_used.clear()


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL не настроен")
    if psycopg2 is None:
        if os.getenv("TESTING") == "true":
            return None
        raise RuntimeError("Не установлена зависимость psycopg2-binary")
    return _checkout(_get_pool(database_url))


@contextmanager
def _transaction():
    connection = get_connection()
    try:
        yield connection
        if connection:
            connection.commit()
    except Exception as exc:
        if connection:
            try:
                connection.rollback()
            except Exception as rollback_exc:
                # Keep the original error primary; report the rollback failure
                # alongside it instead of letting it replace the original.
                logger.error("Rollback после ошибки транзакции не удался", exc_info=rollback_exc)
                if hasattr(exc, "add_note"):  # Python 3.11+
                    exc.add_note(f"Rollback также не удался: {rollback_exc!r}")
        raise
    finally:
        if connection:
            connection.close()


def _validate_operation(operation_type, amount, category):
    try:
        amount = Decimal(str(amount).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception as exc:
        raise InvalidInput("Сумма должна быть числом") from exc
    if amount <= 0:
        raise InvalidInput("Сумма должна быть больше нуля")
    if operation_type not in VALID_OPERATION_TYPES:
        raise InvalidInput("Недопустимый тип операции")
    category = category.strip() if isinstance(category, str) else category
    if operation_type == "Расход" and not category:
        raise InvalidInput("Для расхода нужна категория")
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
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 1b. Safe migration: add token_version to existing users table
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'token_version'
                    ) THEN
                        ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0;
                    END IF;
                END $$;
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

            # 6. Performance indexes — created IF NOT EXISTS, safe to re-run
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_operations_user_id ON {TABLE_NAME} (user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_budgets_user_id ON budgets (user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_user_id ON goals (user_id)")

            # 7. CSV import de-duplication: a statement row's fingerprint is unique
            # per user, so re-importing the same file cannot create duplicates.
            cursor.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS import_hash TEXT")
            cursor.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_user_import_hash "
                f"ON {TABLE_NAME} (user_id, import_hash) WHERE import_hash IS NOT NULL"
            )


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
            cursor.execute("SELECT id, email, password_hash, created_at, token_version FROM users WHERE email = %s", (email,))
            return cursor.fetchone()
    finally:
        connection.close()


def get_user_by_id(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email, password_hash, created_at, token_version FROM users WHERE id = %s", (user_id,))
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
            # Increment token_version to invalidate all existing tokens
            cursor.execute(
                "UPDATE users SET password_hash = %s, token_version = token_version + 1 WHERE id = %s",
                (new_password_hash, user_id)
            )


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


# CSV import
IMPORT_BATCH_SIZE = 500


def find_imported_hashes(user_id: int, hashes):
    """Returns the subset of ``hashes`` this user has already imported."""
    hashes = list(hashes)
    if not hashes:
        return set()
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT import_hash FROM {TABLE_NAME} WHERE user_id = %s AND import_hash = ANY(%s)",
                (user_id, hashes),
            )
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def import_operations(user_id: int, operations):
    """Inserts (date, operation_type, amount, category, import_hash) tuples in one
    transaction and returns the hashes that were actually inserted.

    Rows whose hash the user already has are skipped by the unique index, so a
    repeated import is harmless. Any database error rolls back the whole import.
    """
    validated = []
    for date, operation_type, amount, category, import_hash in operations:
        amount, category = _validate_operation(operation_type, amount, category)
        validated.append((date, operation_type, amount, category, import_hash))

    inserted = set()
    if not validated:
        return inserted
    with _transaction() as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(validated), IMPORT_BATCH_SIZE):
                batch = validated[start:start + IMPORT_BATCH_SIZE]
                placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(batch))
                params = [value for row in batch for value in (user_id, *row)]
                cursor.execute(
                    f"""INSERT INTO {TABLE_NAME} (user_id, date, operation_type, amount, category, import_hash)
                        VALUES {placeholders}
                        ON CONFLICT (user_id, import_hash) WHERE import_hash IS NOT NULL DO NOTHING
                        RETURNING import_hash""",
                    params,
                )
                inserted.update(row[0] for row in cursor.fetchall())
    return inserted


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
        raise InvalidInput("Сумма бюджета должна быть числом") from exc
    if amount <= 0:
        raise InvalidInput("Сумма бюджета должна быть больше нуля")
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
        raise InvalidInput("Сумма бюджета должна быть числом") from exc
    if amount <= 0:
        raise InvalidInput("Сумма бюджета должна быть больше нуля")

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
        raise InvalidInput("Суммы цели должны быть числами") from exc
    if target_amount <= 0:
        raise InvalidInput("Целевая сумма должна быть больше нуля")
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
        raise InvalidInput("Сумма должна быть числом") from exc
    if current_amount < 0:
        raise InvalidInput("Накопленная сумма не может быть отрицательной")

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
