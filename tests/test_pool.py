import os
import subprocess
import sys
import threading

import pytest

import database

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import extensions  # noqa: E402

# The real psycopg2.pool.ThreadedConnectionPool is used; only psycopg2.connect
# is replaced, so the pool's own idle/close rules are exercised.


class FakeInfo:
    def __init__(self, connection): self.connection = connection

    @property
    def transaction_status(self):
        if self.connection.in_transaction:
            return extensions.TRANSACTION_STATUS_INTRANS
        return extensions.TRANSACTION_STATUS_IDLE


class FakeCursor:
    def __init__(self, connection): self.connection = connection
    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, sql, params=None):
        if self.connection.dead:
            self.connection.closed = 2
            raise psycopg2.OperationalError("server closed the connection")
        self.connection.in_transaction = True


class FakeConnection:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.closed = 0
        self.dead = False
        self.in_transaction = False
        self.info = FakeInfo(self)

    def cursor(self): return FakeCursor(self)
    def commit(self): self.in_transaction = False

    def rollback(self):
        if self.dead:
            raise psycopg2.OperationalError("server closed the connection")
        self.in_transaction = False

    def close(self): self.closed = 1


def connect():
    # Other test modules overwrite database.get_connection globally, so go
    # through the pool helpers directly.
    return database._checkout(database._get_pool("postgresql://test"))


@pytest.fixture
def created(monkeypatch):
    connections = []

    def fake_connect(*args, **kwargs):
        connections.append(FakeConnection(*args, **kwargs))
        return connections[-1]

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setattr(database, "get_connection", connect)
    monkeypatch.setattr(database, "POOL_MIN", 1)
    database.close_pool()
    yield connections
    database.close_pool()


def idle():
    return database._pool._pool


def test_connection_is_reused_instead_of_reopened(created):
    first = connect()
    raw = first._raw
    first.close()
    second = connect()
    assert second._raw is raw
    second.close()
    assert len(created) == 1
    assert raw.closed == 0


def test_pool_min_connections_survive_concurrent_burst(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 5)
    database.close_pool()
    for _ in range(3):
        held = [connect() for _ in range(5)]
        for connection in held:
            connection.close()
    assert len(created) == 5
    assert len(idle()) == 5


def test_pool_min_is_capped_by_pool_max(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 20)
    monkeypatch.setattr(database, "POOL_MAX", 3)
    database.close_pool()
    connect().close()
    assert len(created) == 3


def test_open_read_transaction_is_rolled_back_on_return(created):
    connection = connect()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    raw = connection._raw
    connection.close()
    assert raw.in_transaction is False
    assert raw in idle()


def test_dead_idle_connection_is_replaced(created, monkeypatch):
    connection = connect()
    raw = connection._raw
    connection.close()
    raw.dead = True
    monkeypatch.setattr(database, "POOL_IDLE_PING_SECONDS", 0)

    fresh = connect()
    assert fresh._raw is not raw
    assert raw.closed
    fresh.close()


def test_connection_broken_during_request_is_discarded(created):
    connection = connect()
    raw = connection._raw
    raw.dead = True
    with pytest.raises(psycopg2.OperationalError):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    connection.close()
    assert raw not in idle()
    assert database._pool.slots._value == database.POOL_MAX


def test_double_close_returns_connection_once(created):
    connection = connect()
    connection.close()
    connection.close()
    assert len(idle()) == 1


def test_exhausted_pool_times_out(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MAX", 1)
    monkeypatch.setattr(database, "POOL_TIMEOUT", 0.05)
    database.close_pool()
    held = connect()
    with pytest.raises(RuntimeError, match="исчерпан"):
        connect()
    held.close()
    connect().close()


def test_waiting_caller_gets_connection_when_released(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MAX", 1)
    database.close_pool()
    held = connect()
    threading.Timer(0.05, held.close).start()
    connect().close()
    assert len(created) == 1


def test_transaction_helper_returns_connection_to_pool(created):
    with database._transaction():
        pass
    assert len(idle()) == 1


def test_exception_in_transaction_does_not_leak_slots(created):
    for _ in range(20):
        with pytest.raises(ValueError):
            with database._transaction():
                raise ValueError("boom")
    assert database._pool.slots._value == database.POOL_MAX
    assert not database._pool._used


def test_late_close_after_pool_recreated(created):
    held = connect()
    database.close_pool()
    connect().close()  # new pool
    held.close()
    assert held._raw is None
    assert database._pool.slots._value == database.POOL_MAX


def test_original_error_is_not_masked_by_late_close(created):
    with pytest.raises(ValueError, match="business"):
        with database._transaction():
            database.close_pool()
            database._get_pool("postgresql://test")
            raise ValueError("business error")


def test_surplus_connections_do_not_stay_in_last_used(created):
    held = [connect() for _ in range(5)]
    for connection in held:
        connection.close()
    assert len(idle()) == 1
    assert len(database._last_used) == 1


def test_parallel_requests_share_pool_safely(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 3)
    monkeypatch.setattr(database, "POOL_MAX", 3)
    database.close_pool()
    errors = []

    def worker():
        for _ in range(100):
            try:
                with database._transaction() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert database._pool.slots._value == 3
    assert not database._pool._used
    assert len(created) <= 3


def test_failed_rollback_keeps_error_and_discards_connection(created):
    with pytest.raises(psycopg2.OperationalError, match="server closed"):
        with database._transaction() as conn:
            raw = conn._raw
            raw.dead = True  # query and rollback both fail
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
    assert raw.closed
    assert raw not in idle()
    assert database._pool.slots._value == database.POOL_MAX


def test_connect_timeout_applies_to_every_pool_connection(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 2)
    database.close_pool()
    held = [connect() for _ in range(3)]  # 2 opened at startup + 1 on demand
    for connection in held:
        connection.close()
    for raw in idle():
        raw.dead = True
    monkeypatch.setattr(database, "POOL_IDLE_PING_SECONDS", 0)
    connect().close()  # dead connections replaced by a new one
    assert len(created) == 4
    assert all(c.kwargs.get("connect_timeout") == database.CONNECT_TIMEOUT == 10 for c in created)


def test_all_idle_connections_dead_after_neon_suspend(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 5)
    database.close_pool()
    held = [connect() for _ in range(5)]
    for connection in held:
        connection.close()
    stale = list(idle())
    for raw in stale:
        raw.dead = True
    monkeypatch.setattr(database, "POOL_IDLE_PING_SECONDS", 0)

    connection = connect()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")  # works on a fresh connection
    assert connection._raw not in stale
    connection.close()
    assert all(raw.closed for raw in stale)
    assert database._pool.slots._value == database.POOL_MAX


def test_pool_min_above_max_is_capped_with_warning(created, monkeypatch, caplog):
    monkeypatch.setattr(database, "POOL_MIN", 8)
    monkeypatch.setattr(database, "POOL_MAX", 2)
    database.close_pool()
    connect().close()
    assert len(created) == 2
    assert "DB_POOL_MIN (8) больше DB_POOL_MAX (2)" in caplog.text


@pytest.mark.parametrize("name", ["DB_POOL_MIN", "DB_POOL_MAX"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "2.5", ""])
def test_invalid_pool_size_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"{name} должен быть целым положительным числом"):
        database._env_positive_int(name, 5)


@pytest.mark.parametrize("value", ["0", "-3", "abc", "nan", "inf", ""])
def test_invalid_pool_timeout_is_rejected(monkeypatch, value):
    monkeypatch.setenv("DB_POOL_TIMEOUT", value)
    with pytest.raises(ValueError, match="DB_POOL_TIMEOUT должен быть положительным числом секунд"):
        database._env_positive_seconds("DB_POOL_TIMEOUT", 10)


def test_valid_pool_settings_and_defaults(monkeypatch):
    monkeypatch.setenv("DB_POOL_MAX", " 20 ")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "2.5")
    monkeypatch.delenv("DB_POOL_MIN", raising=False)
    assert database._env_positive_int("DB_POOL_MAX", 10) == 20
    assert database._env_positive_seconds("DB_POOL_TIMEOUT", 10) == 2.5
    assert database._env_positive_int("DB_POOL_MIN", 5) == 5


def test_invalid_setting_fails_on_import_with_clear_message():
    env = dict(os.environ, DB_POOL_MAX="0")
    result = subprocess.run(
        [sys.executable, "-c", "import database"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "DB_POOL_MAX должен быть целым положительным числом, получено: '0'" in result.stderr


def test_failed_connect_on_checkout_releases_slot(created, monkeypatch):
    held = connect()  # pool exists; the only idle connection is now in use

    def unreachable(*args, **kwargs):
        raise psycopg2.OperationalError("timeout expired")

    monkeypatch.setattr(psycopg2, "connect", unreachable)
    with pytest.raises(psycopg2.OperationalError, match="timeout expired"):
        connect()
    held.close()
    assert database._pool.slots._value == database.POOL_MAX
    assert not database._pool._used


def test_failed_pool_creation_is_retried(created, monkeypatch):
    real_connect = psycopg2.connect
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(
        psycopg2.OperationalError("could not connect")))
    with pytest.raises(psycopg2.OperationalError):
        connect()
    assert database._pool is None

    monkeypatch.setattr(psycopg2, "connect", real_connect)
    connect().close()
    assert database._pool is not None


def test_concurrent_first_use_creates_single_pool(created, monkeypatch):
    monkeypatch.setattr(database, "POOL_MIN", 2)
    barrier = threading.Barrier(10)
    pools = []

    def first_use():
        barrier.wait()
        pools.append(database._get_pool("postgresql://test"))

    threads = [threading.Thread(target=first_use) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(pool) for pool in pools}) == 1
    assert len(created) == 2


def test_commit_error_rolls_back_and_keeps_live_connection(created):
    connection = connect()
    raw = connection._raw
    connection.close()

    def failing_commit():
        raise psycopg2.OperationalError("could not serialize access")

    raw.commit = failing_commit
    with pytest.raises(psycopg2.OperationalError, match="serialize"):
        with database._transaction() as conn:
            assert conn._raw is raw
            with conn.cursor() as cursor:
                cursor.execute("UPDATE")
    assert raw.in_transaction is False
    assert raw in idle()


def test_fastapi_shutdown_closes_pool_and_next_start_recreates_it(created, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import api

    assert api.shutdown in api.app.router.on_shutdown
    # Other test modules clear on_startup globally; restore the app's own hooks.
    monkeypatch.setattr(api.app.router, "on_startup", [api.startup])
    monkeypatch.setattr(api.app.router, "on_shutdown", [api.shutdown])
    monkeypatch.setattr(database, "create_database", lambda: connect().close())
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
        first_pool = database._pool
        first_raw = idle()[0]
    assert database._pool is None
    assert first_pool.closed
    assert first_raw.closed

    with TestClient(api.app):
        assert database._pool is not None
        assert database._pool is not first_pool


def _half_open_execute(self, sql, params=None):
    # The socket died without psycopg2 noticing: closed stays 0, so only our
    # rollback-and-discard on return keeps the connection out of the pool.
    if self.connection.dead:
        raise psycopg2.OperationalError("server closed the connection")
    self.connection.in_transaction = True


@pytest.mark.parametrize("half_open", [False, True], ids=["closed", "half-open"])
def test_parallel_requests_recover_from_mid_request_connection_failures(created, monkeypatch, half_open):
    # Each worker breaks its own connection on a fixed schedule, so the outcome
    # of every request is known in advance regardless of thread scheduling.
    if half_open:
        monkeypatch.setattr(FakeCursor, "execute", _half_open_execute)
    monkeypatch.setattr(database, "POOL_MIN", 3)
    monkeypatch.setattr(database, "POOL_MAX", 4)
    monkeypatch.setattr(database, "POOL_IDLE_PING_SECONDS", 0)
    database.close_pool()
    workers, steps = 8, 100

    def breaks(worker, step):
        return (worker + step) % 10 == 0

    outcomes = [[] for _ in range(workers)]
    start = threading.Barrier(workers)

    def run(worker):
        start.wait()
        for step in range(steps):
            try:
                with database._transaction() as connection:
                    if breaks(worker, step):
                        connection._raw.dead = True  # dies mid-request
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                outcomes[worker].append("ok")
            except psycopg2.OperationalError:
                outcomes[worker].append("failed")
            except Exception as exc:
                outcomes[worker].append(repr(exc))

    threads = [threading.Thread(target=run, args=(worker,)) for worker in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for worker in range(workers):
        expected = ["failed" if breaks(worker, step) else "ok" for step in range(steps)]
        assert outcomes[worker] == expected  # every request after a failure succeeds
    broken = [raw for raw in created if raw.dead]
    assert len(broken) == workers * steps // 10
    assert all(raw.closed for raw in broken)
    assert database._pool.slots._value == 4
    assert not database._pool._used
    assert all(not raw.dead for raw in idle())
    assert len(database._last_used) <= len(idle())
