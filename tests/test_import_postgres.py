"""Optional checks against the local PostgreSQL sandbox.

Skipped unless FT_TEST_POSTGRES_URL is set. The sandbox is one local, disposable
database with a dedicated role and the schema ft_test, marked by tests/pg_sandbox.py
(see tests/db_safety.py and the README). Nothing else is ever connected: every
statement below runs in the ft_test schema, and the tests drop and recreate the
application tables there.
"""
import threading

import pytest

import bank_import
import database
from db_safety import sandbox_configured, sandbox_sql as _sql

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]
psycopg2 = pytest.importorskip("psycopg2")

LEGACY_SCHEMA = """
    CREATE TABLE users (id BIGSERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                        token_version INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE operations (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                             date TEXT NOT NULL, operation_type TEXT NOT NULL CHECK (operation_type IN ('Доход', 'Расход')),
                             amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0), category TEXT);
"""


@pytest.fixture
def pg(pg_database_url):
    database.close_pool()
    _sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
         "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    _sql(LEGACY_SCHEMA)  # a database created before CSV import existed
    _sql("INSERT INTO users (email, password_hash) VALUES ('a@example.com', 'x'), ('b@example.com', 'x')")
    _sql("INSERT INTO operations (user_id, date, operation_type, amount, category) "
         "VALUES (1, '01.01.2026, 10:00:00', 'Доход', 100, 'Зарплата')")
    yield
    database.close_pool()


def _rows(raw):
    settings = bank_import.ImportSettings(date_column=0, description_column=1, amount_column=2)
    result = bank_import.parse_statement(raw, settings, "KZT")
    return [(r.date, r.operation_type, r.amount, r.category, r.import_hash) for r in result.rows]


def test_migration_on_existing_database_is_repeatable(pg):
    database.create_database()
    database.create_database()
    assert _sql("SELECT import_hash FROM operations") == [(None,)]
    index, = _sql("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_operations_user_import_hash'")
    assert "UNIQUE" in index[0] and "WHERE (import_hash IS NOT NULL)" in index[0]


def test_import_dedup_and_isolation_on_postgres(pg):
    database.create_database()
    batch = _rows("d;desc;a\n16.01.2026;Кофе;-500\n16.01.2026;Кофе;-500\n".encode())
    assert len(database.import_operations(1, batch)) == 2
    assert database.import_operations(1, batch) == set()
    assert len(database.import_operations(2, batch)) == 2
    assert _sql("SELECT user_id, count(*) FROM operations GROUP BY user_id ORDER BY user_id") == [(1, 3), (2, 2)]


WORKER_TIMEOUT = 30  # seconds a barrier or a thread may take; the sandbox answers in milliseconds


def run_concurrently(jobs):
    """Runs each job in its own thread, released together, and returns the values in order.

    Nothing waits forever: the barrier and every join have WORKER_TIMEOUT, and a thread still running after
    that fails the test. A job's exception is kept with its thread, and the test fails with it (as the cause)
    once every thread has ended. A thread that cannot pass the barrier gets BrokenBarrierError, because the
    barrier is aborted when the test stops waiting, so no other thread stays blocked."""
    barrier = threading.Barrier(len(jobs), timeout=WORKER_TIMEOUT)
    outcomes = [None] * len(jobs)

    def worker(index):
        try:
            barrier.wait()
        except threading.BrokenBarrierError as exc:
            outcomes[index] = ("error", exc)
            return
        try:
            outcomes[index] = ("ok", jobs[index]())
        except BaseException as exc:  # kept here and raised by the test, never lost with the thread
            outcomes[index] = ("error", exc)

    threads = [threading.Thread(target=worker, args=(index,), name="worker-%d" % index)
               for index in range(len(jobs))]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WORKER_TIMEOUT)
    finally:
        barrier.abort()  # releases a thread still waiting on the barrier; harmless once every thread has passed it
    stuck = [thread.name for thread in threads if thread.is_alive()]
    assert not stuck, "threads still running %d s after the start: %s" % (WORKER_TIMEOUT, ", ".join(stuck))
    failed = [value for kind, value in outcomes if kind == "error"]
    if failed:
        raise AssertionError("%d of %d worker(s) failed:\n%s" % (
            len(failed), len(jobs), "\n".join(repr(error) for error in failed))) from failed[0]
    return [value for _, value in outcomes]


def test_concurrent_imports_do_not_duplicate(pg):
    database.create_database()
    batch = _rows(("d;desc;a\n" + "".join(f"{d:02d}.02.2026;Покупка;-{d}0\n" for d in range(1, 21))).encode())
    inserted = run_concurrently([lambda: len(database.import_operations(1, batch))] * 4)
    assert sum(inserted) == 20
    assert _sql("SELECT count(*) FROM operations WHERE import_hash IS NOT NULL") == [(20,)]


def test_database_error_rolls_back_every_batch(pg, monkeypatch):
    database.create_database()
    monkeypatch.setattr(database, "IMPORT_BATCH_SIZE", 2)
    monkeypatch.setattr(database, "_validate_operation", lambda kind, amount, category: (amount, category))
    batch = _rows("d;desc;a\n01.03.2026;a;-1\n02.03.2026;b;-2\n".encode())
    batch.append(("03.03.2026, 00:00:00", "Расход", -3, "x", "negative"))  # violates CHECK (amount > 0)
    with pytest.raises(psycopg2.IntegrityError):
        database.import_operations(1, batch)
    assert _sql("SELECT count(*) FROM operations WHERE import_hash IS NOT NULL") == [(0,)]
