import logging
import os
import threading
import time
import unicodedata
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal

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

import budget_alerts

load_dotenv()
logger = logging.getLogger(__name__)
TABLE_NAME = "operations"
VALID_OPERATION_TYPES = ("Доход", "Расход")


class InvalidInput(ValueError):
    """Data the user sent is not acceptable. The message is meant for the client;
    any other ValueError (configuration, driver) is an internal error."""


# The amount columns are NUMERIC(14, 2): a cent is the smallest step and this
# the largest value they hold. Anything else would fail in the database.
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")
AMOUNT_TOO_LARGE = "Сумма слишком большая"


def _money(value, not_a_number):
    """The amount as the database will store it, rounded to cents."""
    try:
        amount = Decimal(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except Exception as exc:
        raise InvalidInput(not_a_number) from exc
    if not amount.is_finite():
        raise InvalidInput(not_a_number)
    if amount < 0:
        return amount  # every caller rejects negative amounts with its own message
    if amount >= MAX_AMOUNT + CENT:
        raise InvalidInput(AMOUNT_TOO_LARGE)  # also keeps quantize() within Decimal's precision
    # PostgreSQL rounds half away from zero; .995 past the maximum overflows there too.
    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if amount > MAX_AMOUNT:
        raise InvalidInput(AMOUNT_TOO_LARGE)
    return amount


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


class CategoryNotFound(LookupError):
    pass


class CategoryExists(Exception):
    """The user already has a category of that kind with that name (any letter case)."""


class CategoryInUse(Exception):
    """The category cannot be deleted while a budget is assigned to it."""


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
    amount = _money(amount, "Сумма должна быть числом")
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

            # 5b. Budget alert switches. Both are on for everyone, existing users
            # included; ADD COLUMN ... IF NOT EXISTS makes the migration repeatable.
            cursor.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS budget_alerts_inapp BOOLEAN NOT NULL DEFAULT TRUE")
            cursor.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS budget_alerts_push BOOLEAN NOT NULL DEFAULT TRUE")

            # 5c. The highest alert level already reported for a budget in a calendar
            # month ('YYYY-MM'). It is what keeps one crossing from being reported twice.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS budget_alert_state (
                    budget_id BIGINT NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
                    month TEXT NOT NULL,
                    level SMALLINT NOT NULL DEFAULT 0 CHECK (level BETWEEN 0 AND 3),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (budget_id, month)
                )
            """)

            # 5d. Categories a user made for themselves (the built-in ones are in the code,
            # BUILTIN_CATEGORIES). Operations and budgets keep the category as plain text, so
            # nothing references this table; it only records which names the user manages.
            # The unique index is a backstop: add/rename also compare names in Python (case
            # folding that does not depend on the database's locale) under a lock on the user's row.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_categories (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('Доход', 'Расход')),
                    name TEXT NOT NULL
                )
            """)
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_categories_user_kind_name "
                "ON user_categories (user_id, kind, lower(name))"
            )

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


@contextmanager
def locked_user_for_token(user_id: int):
    """The user row, read under a shared row lock that lasts until the block ends (the transaction
    commits then). A logout or a password change writes this row, and an UPDATE waits for a shared
    lock, so neither can commit while the caller is still reading the row and issuing its token.
    The caller compares the password hash it checked with this row: a changed hash means a password
    change came first. The transaction takes this one row lock and waits for nothing else, so it cannot
    be part of a cycle of waiting transactions."""
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, email, password_hash, created_at, token_version FROM users WHERE id = %s FOR SHARE",
                (user_id,),
            )
            yield cursor.fetchone()


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
                "SELECT language, theme, currency, budget_alerts_inapp, budget_alerts_push "
                "FROM user_settings WHERE user_id = %s",
                (user_id,)
            )
            row = cursor.fetchone()
            if not row:
                # Fallback default if not present
                return {"language": "RU", "theme": "light", "currency": "KZT",
                        "budget_alerts_inapp": True, "budget_alerts_push": True}
            return {"language": row[0], "theme": row[1], "currency": row[2],
                    "budget_alerts_inapp": bool(row[3]), "budget_alerts_push": bool(row[4])}
    finally:
        connection.close()


def update_user_settings(user_id: int, language: str = None, theme: str = None, currency: str = None,
                         budget_alerts_inapp: bool = None, budget_alerts_push: bool = None):
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
            if budget_alerts_inapp is not None:
                updates.append("budget_alerts_inapp = %s")
                params.append(bool(budget_alerts_inapp))
            if budget_alerts_push is not None:
                updates.append("budget_alerts_push = %s")
                params.append(bool(budget_alerts_push))

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


def revoke_all_sessions(user_id: int):
    """Bumps token_version so every token issued before now stops working, on every device."""
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user_id,))


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
    """Deletes the operation and returns what it was, as (type, category, date), so
    the budgets it counted against can be recalculated."""
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {TABLE_NAME} WHERE id = %s AND user_id = %s RETURNING operation_type, category, date",
                (operation_id, user_id)
            )
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")
            return cursor.fetchone()


def update_operation(operation_id: int, user_id: int, date, operation_type, amount, category=None):
    """Updates the operation and returns what it was before, as (type, category, date):
    the old category and month lose the amount, the new ones gain it."""
    amount, category = _validate_operation(operation_type, amount, category)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT operation_type, category, date FROM {TABLE_NAME} WHERE id = %s AND user_id = %s FOR UPDATE",
                (operation_id, user_id),
            )
            previous = cursor.fetchone()
            cursor.execute(
                f"""UPDATE {TABLE_NAME}
                    SET date = %s, operation_type = %s, amount = %s, category = %s
                    WHERE id = %s AND user_id = %s""",
                (date, operation_type, amount, category, operation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise OperationNotFound("Операция не найдена")
            return previous


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
    amount = _money(amount, "Сумма бюджета должна быть числом")
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
    amount = _money(amount, "Сумма бюджета должна быть числом")
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


def evaluate_budget_alerts(user_id: int, targets):
    """Recalculates the alert level of the user's budgets and returns the alerts to send.

    ``targets`` is an iterable of (category, month, notify): the budgets of that
    category are measured against the spending of that calendar month ('YYYY-MM').
    The level reached so far is kept per budget and month. A higher level is an
    alert (when ``notify``), a lower or equal one is not; the stored level always
    follows the spending, so crossing a threshold again later alerts again.

    The state row is locked before the spending is read, so two requests for the same
    budget run one after the other and a crossing is reported exactly once.
    """
    wanted = {}
    for category, month, notify in targets:
        if category and month:
            wanted[(category, month)] = wanted.get((category, month), False) or bool(notify)
    if not wanted:
        return []

    alerts = []
    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, category, amount FROM budgets WHERE user_id = %s AND category = ANY(%s) ORDER BY id",
                (user_id, sorted({category for category, _ in wanted})),
            )
            budgets = cursor.fetchall()
            # One global lock order (budget, month) keeps concurrent requests from deadlocking.
            work = sorted(
                ((budget_id, category, amount, month, notify)
                 for budget_id, category, amount in budgets
                 for (wanted_category, month), notify in wanted.items() if wanted_category == category),
                key=lambda item: (item[0], item[3]),
            )
            for budget_id, category, amount, month, notify in work:
                cursor.execute(
                    "INSERT INTO budget_alert_state (budget_id, month) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (budget_id, month),
                )
                cursor.execute(
                    "SELECT level FROM budget_alert_state WHERE budget_id = %s AND month = %s FOR UPDATE",
                    (budget_id, month),
                )
                previous = cursor.fetchone()[0]
                cursor.execute(
                    f"SELECT date, amount FROM {TABLE_NAME} WHERE user_id = %s AND category = %s AND operation_type = %s",
                    (user_id, category, "Расход"),
                )
                spent = budget_alerts.month_spent(cursor.fetchall(), month)
                level = budget_alerts.level_for(spent, amount)
                if level != previous:
                    cursor.execute(
                        "UPDATE budget_alert_state SET level = %s, updated_at = CURRENT_TIMESTAMP "
                        "WHERE budget_id = %s AND month = %s",
                        (level, budget_id, month),
                    )
                if notify and level > previous:
                    alerts.append(budget_alerts.make_alert(budget_id, category, month, level, spent, amount))
    return alerts


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
    target_amount = _money(target_amount, "Суммы цели должны быть числами")
    current_amount = _money(current_amount, "Суммы цели должны быть числами")
    if target_amount <= 0:
        raise InvalidInput("Целевая сумма должна быть больше нуля")
    if current_amount < 0:
        raise InvalidInput("Накопленная сумма не может быть отрицательной")
    title = title.strip()

    with _transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO goals (user_id, title, target_amount, current_amount, deadline) VALUES (%s, %s, %s, %s, %s)",
                (user_id, title, target_amount, current_amount, deadline)
            )


def update_goal_progress(goal_id: int, user_id: int, current_amount):
    current_amount = _money(current_amount, "Сумма должна быть числом")
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


# User categories
# The built-in categories, per operation type. The frontend shows the same ones (its lists
# are in frontend/script.js); here they are needed to refuse user categories that would
# take their names and to choose where deleted categories' operations go.
BUILTIN_CATEGORIES = {
    "Доход": ("Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое"),
    "Расход": ("Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"),
}
# The same names in English and Kazakh as frontend/i18n.js shows them. A user category may
# not take any of the three names of a built-in one.
_BUILTIN_CATEGORY_TRANSLATIONS = {
    "Зарплата": ("Salary", "Жалақы"),
    "Фриланс": ("Freelance",),
    "Бизнес": ("Business",),
    "Инвестиции": ("Investments", "Инвестициялар"),
    "Еда": ("Food", "Тамақ"),
    "Транспорт": ("Transport", "Көлік"),
    "Дом": ("Home", "Үй"),
    "Развлечения": ("Entertainment", "Ойын-сауық"),
    "Здоровье": ("Health", "Денсаулық"),
    "Другое": ("Other", "Басқа"),
}
DEFAULT_MOVE_CATEGORY = "Другое"
MAX_USER_CATEGORIES = 50
MAX_CATEGORY_NAME_LENGTH = 50
_MAX_ID = 2 ** 63 - 1  # BIGINT

CATEGORY_NAME_LENGTH_MESSAGE = f"Название категории должно содержать от 1 до {MAX_CATEGORY_NAME_LENGTH} символов"
CATEGORY_NAME_CHARACTERS_MESSAGE = "Название категории содержит недопустимые символы"
CATEGORY_NAME_BUILTIN_MESSAGE = "Название совпадает со встроенной категорией"
CATEGORY_KIND_MESSAGE = "Недопустимый тип категории"
CATEGORY_LIMIT_MESSAGE = f"Достигнут лимит пользовательских категорий ({MAX_USER_CATEGORIES})"
CATEGORY_EXISTS_MESSAGE = "Категория с таким названием уже существует"
CATEGORY_NOT_FOUND_MESSAGE = "Категория не найдена"
CATEGORY_MOVE_NOT_FOUND_MESSAGE = "Категория для переноса не найдена"
CATEGORY_MOVE_SAME_MESSAGE = "Нельзя перенести операции в удаляемую категорию"
CATEGORY_IN_USE_MESSAGE = "На категорию назначен бюджет: удалите или измените бюджет и повторите"


def _fold(text):
    return text.casefold()


def _builtin_names_folded():
    """folded name in any language -> the built-in category's name (Russian)."""
    names = {}
    for kind_names in BUILTIN_CATEGORIES.values():
        for name in kind_names:
            for variant in (name, *_BUILTIN_CATEGORY_TRANSLATIONS.get(name, ())):
                names[_fold(variant)] = name
    return names


def _clean_category_name(name):
    """The name as it is stored, or InvalidInput: trimmed, 1-50 characters, no control
    characters (NUL included), not the name of a built-in category in any language."""
    if not isinstance(name, str):
        raise InvalidInput("Название категории должно быть текстом")
    name = unicodedata.normalize("NFC", name).strip()
    if not 1 <= len(name) <= MAX_CATEGORY_NAME_LENGTH:
        raise InvalidInput(CATEGORY_NAME_LENGTH_MESSAGE)
    if any(unicodedata.category(char) in ("Cc", "Cs") for char in name):
        raise InvalidInput(CATEGORY_NAME_CHARACTERS_MESSAGE)
    if _fold(name) in _builtin_names_folded():
        raise InvalidInput(CATEGORY_NAME_BUILTIN_MESSAGE)
    return name


def _check_category_kind(kind):
    if kind not in BUILTIN_CATEGORIES:
        raise InvalidInput(CATEGORY_KIND_MESSAGE)


def _category_id(category_id):
    """An id that can exist, or CategoryNotFound (a number the BIGINT column cannot hold
    would otherwise be a database error)."""
    if isinstance(category_id, bool) or not isinstance(category_id, int) or not 1 <= category_id <= _MAX_ID:
        raise CategoryNotFound(CATEGORY_NOT_FOUND_MESSAGE)
    return category_id


def _name_taken(rows, kind, name, skip_id=None):
    return any(row[1] == kind and row[0] != skip_id and _fold(row[2]) == _fold(name) for row in rows)


def _lock_user(cursor, user_id):
    """Serialises everything that changes one user's categories, so the limit and the
    duplicate check cannot be passed by two requests at once."""
    cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
    if cursor.fetchone() is None:
        raise UserNotFound("Пользователь не найден")


def category_items(custom_rows):
    """The built-in categories followed by the user's own ((id, kind, name) rows), as dicts."""
    items = [{"id": None, "kind": kind, "name": name, "builtin": True}
             for kind, names in BUILTIN_CATEGORIES.items() for name in names]
    items += [{"id": row[0], "kind": row[1], "name": row[2], "builtin": False} for row in custom_rows]
    return items


def get_user_categories(user_id: int):
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return cursor.fetchall()
    finally:
        connection.close()


def list_categories(user_id: int):
    return category_items(get_user_categories(user_id))


def add_user_category(user_id: int, kind, name):
    _check_category_kind(kind)
    name = _clean_category_name(name)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            _lock_user(cursor, user_id)
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            rows = cursor.fetchall()
            if _name_taken(rows, kind, name):
                raise CategoryExists(CATEGORY_EXISTS_MESSAGE)
            if len(rows) >= MAX_USER_CATEGORIES:
                raise InvalidInput(CATEGORY_LIMIT_MESSAGE)
            try:
                cursor.execute(
                    "INSERT INTO user_categories (user_id, kind, name) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, kind, name)
                )
            except Exception as exc:
                if "unique constraint" in str(exc).lower() or "duplicate key" in str(exc).lower():
                    raise CategoryExists(CATEGORY_EXISTS_MESSAGE) from exc
                raise
            category_id = cursor.fetchone()[0]
    return {"id": category_id, "kind": kind, "name": name, "builtin": False}


def rename_user_category(category_id: int, user_id: int, new_name):
    """Renames the user's category and, in the same transaction, the category text of the
    user's operations of that kind (and, for expense categories, of the user's budgets).
    Returns the category and how many operations and budgets were updated."""
    new_name = _clean_category_name(new_name)
    category_id = _category_id(category_id)
    operations = budgets = 0
    with _transaction() as connection:
        with connection.cursor() as cursor:
            _lock_user(cursor, user_id)
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE id = %s AND user_id = %s FOR UPDATE",
                (category_id, user_id)
            )
            current = cursor.fetchone()
            if current is None:
                raise CategoryNotFound(CATEGORY_NOT_FOUND_MESSAGE)
            kind, old_name = current[1], current[2]
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            if _name_taken(cursor.fetchall(), kind, new_name, skip_id=category_id):
                raise CategoryExists(CATEGORY_EXISTS_MESSAGE)
            if new_name != old_name:
                cursor.execute(
                    "UPDATE user_categories SET name = %s WHERE id = %s AND user_id = %s",
                    (new_name, category_id, user_id)
                )
                cursor.execute(
                    f"UPDATE {TABLE_NAME} SET category = %s WHERE user_id = %s AND operation_type = %s AND category = %s",
                    (new_name, user_id, kind, old_name)
                )
                operations = cursor.rowcount
                if kind == "Расход":
                    cursor.execute(
                        "UPDATE budgets SET category = %s WHERE user_id = %s AND category = %s",
                        (new_name, user_id, old_name)
                    )
                    budgets = cursor.rowcount
    return {"category": {"id": category_id, "kind": kind, "name": new_name, "builtin": False},
            "operations_updated": operations, "budgets_updated": budgets}


def _move_target(kind, current_name, move_to, rows):
    """The category (as stored) the operations of a deleted category go to: ``move_to``
    resolved case-insensitively to a built-in category of that kind (any of its three
    names) or another category of the user's of that kind; "Другое" when not given."""
    if move_to is None or (isinstance(move_to, str) and not move_to.strip()):
        return DEFAULT_MOVE_CATEGORY
    if not isinstance(move_to, str):
        raise InvalidInput(CATEGORY_MOVE_NOT_FOUND_MESSAGE)
    wanted = _fold(unicodedata.normalize("NFC", move_to).strip())
    if wanted == _fold(current_name):
        raise InvalidInput(CATEGORY_MOVE_SAME_MESSAGE)
    for name in BUILTIN_CATEGORIES[kind]:
        if wanted in {_fold(variant) for variant in (name, *_BUILTIN_CATEGORY_TRANSLATIONS.get(name, ()))}:
            return name
    for row in rows:
        if row[1] == kind and _fold(row[2]) == wanted:
            return row[2]
    raise InvalidInput(CATEGORY_MOVE_NOT_FOUND_MESSAGE)


def delete_user_category(category_id: int, user_id: int, move_to=None):
    """Deletes the user's category after moving the user's operations of that kind from it to
    ``move_to`` ("Другое" by default), in one transaction; the history keeps every operation.
    An expense category with a budget is not deleted (CategoryInUse)."""
    category_id = _category_id(category_id)
    with _transaction() as connection:
        with connection.cursor() as cursor:
            _lock_user(cursor, user_id)
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE id = %s AND user_id = %s FOR UPDATE",
                (category_id, user_id)
            )
            current = cursor.fetchone()
            if current is None:
                raise CategoryNotFound(CATEGORY_NOT_FOUND_MESSAGE)
            kind, name = current[1], current[2]
            cursor.execute(
                "SELECT id, kind, name FROM user_categories WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            target = _move_target(kind, name, move_to, cursor.fetchall())
            if kind == "Расход":
                cursor.execute(
                    "SELECT COUNT(*) FROM budgets WHERE user_id = %s AND category = %s",
                    (user_id, name)
                )
                if cursor.fetchone()[0]:
                    raise CategoryInUse(CATEGORY_IN_USE_MESSAGE)
            cursor.execute(
                f"UPDATE {TABLE_NAME} SET category = %s WHERE user_id = %s AND operation_type = %s AND category = %s",
                (target, user_id, kind, name)
            )
            moved = cursor.rowcount
            cursor.execute(
                "DELETE FROM user_categories WHERE id = %s AND user_id = %s",
                (category_id, user_id)
            )
    return {"kind": kind, "name": name, "moved_to": target, "operations_moved": moved}
