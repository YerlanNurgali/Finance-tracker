"""PostgreSQL tests for sign-in, sign-out, password change and account deletion on one user row.

They run only against the local test sandbox (tests/pg_sandbox.py) and are skipped without
FT_TEST_POSTGRES_URL, like the other *_postgres.py tests. The sandbox gate and the schema checks
of tests/db_safety.py apply to every statement these tests send.

Synchronisation. A concurrent scenario decides the order with a known point, not with time:
  * the sign-in pauses inside its own code path, right after it holds the user's row lock
    (the hold_login fixture), or the test holds an uncommitted statement itself;
  * the other operation is started, and the test waits until PostgreSQL reports that the pid of that
    operation's backend is blocked on a lock that the pid of the other backend holds (pg_stat_activity
    and pg_blocking_pids).
The pids fixture records the backend pid of each pooled connection by the name of the thread that took
it. The name only selects which recorded pid is used; the two pids given to _waits_on are always explicit.
The poll in _wait_until only repeats a check of database state; it never decides the order.

Cleanup. Every concurrent scenario ends in a finally block that releases the paused sign-in, ends any
uncommitted transaction and waits for its threads. A cleanup error never replaces the test's own error.
"""

import logging
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import api
import auth
import database
from db_safety import sandbox_configured, sandbox_sql as sql

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]

EMAIL = "owner@example.test"
PASSWORD = "Secret123!"
NEW_PASSWORD = "NewPwd456!"
CREDENTIALS = {"email": EMAIL, "password": PASSWORD}
TOKEN_REFUSED = "Токен недействителен. Войдите снова"
USER_GONE = "Пользователь не найден"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def pg(pg_database_url):
    """Fresh application tables in the sandbox, built the same way as in the other PostgreSQL modules."""
    database.close_pool()
    sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
        "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    database.create_database()
    api.limiter.reset()  # the sign-in limit is per client address; a test without the client fixture must start at zero
    yield
    database.close_pool()


@pytest.fixture
def client(pg):
    api.limiter.reset()
    yield TestClient(api.app, raise_server_exceptions=False)
    api.limiter.reset()


@pytest.fixture
def pids(monkeypatch):
    """Backend pid of every pooled connection, by the name of the thread that took it."""
    recorded = {}
    lock = threading.Lock()
    real_get_connection = database.get_connection

    def recording_get_connection():
        connection = real_get_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]
        connection.rollback()  # a read-only statement: end its transaction before the caller starts its work
        with lock:
            recorded.setdefault(threading.current_thread().name, []).append(pid)
        return connection

    monkeypatch.setattr(database, "get_connection", recording_get_connection)
    return recorded


@pytest.fixture
def hold_login(monkeypatch):
    """Pauses the sign-in thread after it has issued its token, while its row lock is still held.

    The token is issued inside the locked block of login_user, and the block commits only when
    login_user returns, so the pause keeps the lock. Only the thread named "login" pauses."""
    holding = threading.Event()
    release = threading.Event()
    issued = []
    real_create = auth.create_access_token

    def create_while_locked(data, *args, **kwargs):
        token = real_create(data, *args, **kwargs)
        if threading.current_thread().name == "login":
            issued.append(token)
            holding.set()
            assert release.wait(20), "the sign-in was never released"
        return token

    monkeypatch.setattr(auth, "create_access_token", create_while_locked)
    return SimpleNamespace(holding=holding, release=release, issued=issued)


# ── operations, called the way the routes call them ───────────────────────────

def _request():
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "query_string": b"",
                    "headers": [], "client": ("127.0.0.1", 40000)})


def _signup():
    return database.create_user(EMAIL, auth.hash_password(PASSWORD))


def _login_direct(email, password):
    return api.login_user(request=_request(), payload=api.LoginPayload(email=email, password=password))


def _logout_direct(uid):
    return api.logout(current_user={"id": uid, "email": EMAIL})


def _change_direct(uid, current, new):
    return api.change_password(request=_request(),
                               payload=api.PasswordChangePayload(current_password=current, new_password=new),
                               current_user={"id": uid, "email": EMAIL})


def _delete_direct(uid):
    return api.delete_account(request=_request(), payload=api.DeleteAccountPayload(password=PASSWORD),
                              current_user={"id": uid, "email": EMAIL})


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _me(client, token):
    return client.get("/auth/me", headers={"Authorization": "Bearer " + token})


def _login_status(client, password):
    return client.post("/auth/login", json={"email": EMAIL, "password": password}).status_code


# ── database state ────────────────────────────────────────────────────────────

def _token_version(uid):
    return sql("SELECT token_version FROM ft_test.users WHERE id = %s", (uid,))[0][0]


def _user_count():
    return sql("SELECT count(*) FROM ft_test.users")[0][0]


def _state(pid):
    rows = sql("SELECT state FROM pg_stat_activity WHERE pid = %s", (pid,))
    return rows[0][0] if rows else None


def _idle_in_transaction_elsewhere():
    # The monitoring connection excludes itself by its own pid.
    return sql("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
               "AND state = 'idle in transaction' AND pid <> pg_backend_pid()")[0][0]


def _waits_on(waiter, blocker, statement):
    """True when the backend waiter is blocked on a lock that the backend blocker holds, while running statement.

    Both pids are passed in by the caller. pg_blocking_pids names the sessions holding a lock that the waiter
    needs, so the wait is tied to the expected session, not only to some lock wait with matching text."""
    rows = sql("SELECT count(*) FROM pg_stat_activity WHERE pid = %s AND wait_event_type = 'Lock' "
               "AND query LIKE %s AND %s = ANY(pg_blocking_pids(pid))",
               (waiter, "%" + statement + "%", blocker))
    return rows[0][0] == 1


def _wait_until(condition, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for " + what)
        time.sleep(0.01)  # a poll interval between checks of database state, not the ordering


# ── threads and cleanup ───────────────────────────────────────────────────────

def _start(name, function, *args):
    outcome = {}

    def run():
        try:
            outcome["result"] = function(*args)
        except BaseException as exc:  # reported to the test through outcome["error"]
            outcome["error"] = exc

    thread = threading.Thread(target=run, name=name)
    thread.start()
    return thread, outcome


def _join(thread):
    """Waits at most 20 seconds for the thread. A thread still running after that may still hold a connection or a
    row lock, so the test reports it instead of passing; _cleanup logs this error when the test has already failed."""
    thread.join(20)
    if thread.is_alive():
        raise AssertionError("thread " + thread.name + " is still running 20 seconds after the test released it; "
                             "it may still hold a connection or a row lock")


def _refused(outcome, status):
    error = outcome.get("error")
    return isinstance(error, HTTPException) and error.status_code == status


def _release_uncommitted(holder):
    """Rolls back and returns the connection still held in holder, if the test has not committed it."""
    while holder:
        connection = holder.pop()
        try:
            connection.rollback()
        finally:
            connection.close()


def _cleanup(steps, test_passed):
    """Runs every cleanup step, even after one of them fails.

    Each failing step is logged with its traceback, so pytest shows it in the captured log of a failed test.
    When the test itself failed, its own error is not replaced: the test's error propagates after this returns.
    When the test passed, all cleanup errors are raised together, so the test cannot pass with a stuck
    thread or an open transaction."""
    errors = []
    for step in steps:
        try:
            step()
        except Exception as error:
            logger.error("cleanup step failed", exc_info=error)
            errors.append(error)
    if errors and test_passed:
        raise AssertionError("cleanup failed (%d error(s)):\n%s" % (
            len(errors), "\n".join(repr(error) for error in errors))) from errors[0]


# ── HTTP and sequential behaviour ─────────────────────────────────────────────

def test_a_plain_login_issues_a_working_token(pg, client):
    """Invariant: a sign-in without any concurrency returns a token that works, for version 0."""
    uid = _signup()
    response = client.post("/auth/login", json=CREDENTIALS)
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert auth.verify_access_token(token)["tv"] == 0
    assert _me(client, token).status_code == 200
    assert _token_version(uid) == 0


def test_a_logout_over_http_revokes_every_session_of_the_account(pg, client):
    """Invariant: a logout ends every session of the account. Two independent sign-ins give two tokens; after
    the logout both are refused, not only the token the logout was sent with."""
    uid = _signup()
    first = client.post("/auth/login", json=CREDENTIALS).json()["access_token"]
    second = client.post("/auth/login", json=CREDENTIALS).json()["access_token"]
    assert first != second, "the two sign-ins must give two independent tokens"
    assert _me(client, first).status_code == 200
    assert _me(client, second).status_code == 200
    response = client.post("/auth/logout", headers={"Authorization": "Bearer " + first})
    assert response.status_code == 200
    assert _token_version(uid) == 1
    for token in (first, second):
        me = _me(client, token)
        assert me.status_code == 401
        assert me.json()["detail"] == TOKEN_REFUSED


def test_a_login_after_a_completed_logout_gets_the_committed_version(pg, client):
    """Invariant: a sign-in that reads the row after a logout is committed issues a token for that version."""
    uid = _signup()
    _logout_direct(uid)
    response = client.post("/auth/login", json=CREDENTIALS)
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert auth.verify_access_token(token)["tv"] == 1
    assert _me(client, token).status_code == 200
    assert _token_version(uid) == 1


def test_a_password_change_committed_before_a_login_refuses_the_old_password(pg, client):
    """Invariant: once a password change is committed, the old password does not sign in."""
    uid = _signup()
    database.update_user_password(uid, auth.hash_password(NEW_PASSWORD))
    assert _login_status(client, PASSWORD) == 401
    response = client.post("/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert auth.verify_access_token(token)["tv"] == 1
    assert _me(client, token).status_code == 200


# ── sign-in against sign-out ──────────────────────────────────────────────────

def test_a_logout_that_waits_for_a_login_revokes_the_login_token_after_it(pg, client, pids, hold_login):
    """Invariant: a logout that overlaps a sign-in waits for the sign-in's lock. Its token stays valid until
    the logout commits, and the logout revokes it afterwards, because it committed after the sign-in."""
    uid = _signup()
    steps = [hold_login.release.set]
    passed = False
    try:
        login_thread, login = _start("login", _login_direct, EMAIL, PASSWORD)
        steps.append(lambda: _join(login_thread))
        assert hold_login.holding.wait(20), "the sign-in reached its locked block"
        login_pid = pids["login"][-1]
        logout_thread, logout = _start("logout", _logout_direct, uid)
        steps.append(lambda: _join(logout_thread))
        _wait_until(lambda: bool(pids.get("logout"))
                    and _waits_on(pids["logout"][-1], login_pid, "SET token_version"),
                    "the logout to wait for the sign-in's lock")
        assert _token_version(uid) == 0, "the logout has not committed while the sign-in holds the lock"
        token = hold_login.issued[0]
        assert _me(client, token).status_code == 200, "the token is valid until the revocation commits"
        hold_login.release.set()
        _join(login_thread)
        _join(logout_thread)
        assert "error" not in login and "error" not in logout
        assert login["result"]["access_token"] == token
        assert _token_version(uid) == 1
        me = _me(client, token)
        assert me.status_code == 401
        assert me.json()["detail"] == TOKEN_REFUSED
        passed = True
    finally:
        _cleanup(steps, passed)


# ── sign-in against password change ───────────────────────────────────────────

def test_a_password_change_that_waits_for_a_login_revokes_the_login_and_the_older_tokens(
        pg, client, pids, hold_login):
    """Invariant: a password change that overlaps a sign-in waits for it. The sign-in's token and every token
    issued before the change stop working; the session the change issues works; the old password is refused."""
    uid = _signup()
    old = client.post("/auth/login", json=CREDENTIALS).json()["access_token"]
    steps = [hold_login.release.set]
    passed = False
    try:
        login_thread, login = _start("login", _login_direct, EMAIL, PASSWORD)
        steps.append(lambda: _join(login_thread))
        assert hold_login.holding.wait(20), "the sign-in reached its locked block"
        login_pid = pids["login"][-1]
        change_thread, change = _start("change", _change_direct, uid, PASSWORD, NEW_PASSWORD)
        steps.append(lambda: _join(change_thread))
        _wait_until(lambda: bool(pids.get("change"))
                    and _waits_on(pids["change"][-1], login_pid, "SET password_hash"),
                    "the password change to wait for the sign-in's lock")
        assert _token_version(uid) == 0
        token = hold_login.issued[0]
        assert _me(client, token).status_code == 200, "the token is valid until the change commits"
        hold_login.release.set()
        _join(login_thread)
        _join(change_thread)
        assert "error" not in login and "error" not in change
        new = change["result"]["access_token"]
        assert _token_version(uid) == 1
        assert _me(client, token).status_code == 401, "the sign-in came before the change"
        assert _me(client, old).status_code == 401, "tokens issued before the change are revoked"
        assert _me(client, new).status_code == 200, "the session the change issues works"
        api.limiter.reset()
        assert _login_status(client, PASSWORD) == 401
        assert _login_status(client, NEW_PASSWORD) == 200
        passed = True
    finally:
        _cleanup(steps, passed)


def test_a_login_waiting_for_an_uncommitted_password_change_refuses_the_old_password(pg, pids, client):
    """Invariant: a sign-in that checked the old hash and then waits for a password change that is still in
    progress reads the new hash once the change commits, and refuses the old password.

    This is the PostgreSQL behaviour the hash comparison relies on: FOR SHARE waits for the uncommitted
    UPDATE and then returns the row as the UPDATE left it."""
    uid = _signup()
    change_connection = database.get_connection()
    change_pid = pids["MainThread"][-1]
    open_changes = [change_connection]
    steps = [lambda: _release_uncommitted(open_changes)]
    passed = False
    try:
        with change_connection.cursor() as cursor:
            cursor.execute("UPDATE ft_test.users SET password_hash = %s, token_version = token_version + 1 WHERE id = %s",
                           (auth.hash_password(NEW_PASSWORD), uid))
        login_thread, login = _start("login", _login_direct, EMAIL, PASSWORD)
        steps.append(lambda: _join(login_thread))
        _wait_until(lambda: bool(pids.get("login"))
                    and _waits_on(pids["login"][-1], change_pid, "FOR SHARE"),
                    "the sign-in to wait for the uncommitted change")
        change_connection.commit()
        open_changes.clear()
        change_connection.close()
        _join(login_thread)
        assert _refused(login, 401), login.get("result", login.get("error"))
        assert _token_version(uid) == 1
        api.limiter.reset()
        assert _login_status(client, NEW_PASSWORD) == 200
        passed = True
    finally:
        _cleanup(steps, passed)


def test_a_password_change_between_the_check_and_the_lock_refuses_the_login(pg, client, monkeypatch):
    """Invariant: the hash checked before the lock must still be the stored hash under the lock.
    A change committed in between makes the login fail with 401 and issues no token."""
    uid = _signup()
    real_verify = auth.verify_password

    def check_then_change(stored, provided):
        ok = real_verify(stored, provided)
        if ok:
            database.update_user_password(uid, auth.hash_password(NEW_PASSWORD))
        return ok

    monkeypatch.setattr(auth, "verify_password", check_then_change)
    with pytest.raises(HTTPException) as refused:
        _login_direct(EMAIL, PASSWORD)
    assert refused.value.status_code == 401
    monkeypatch.setattr(auth, "verify_password", real_verify)
    assert _token_version(uid) == 1
    api.limiter.reset()
    assert _login_status(client, NEW_PASSWORD) == 200


def test_a_logout_between_the_check_and_the_lock_gives_a_working_token(pg, client, monkeypatch):
    """Invariant: a logout committed after the check and before the lock is seen by the lock: the token
    carries the committed version and works."""
    uid = _signup()
    real_verify = auth.verify_password

    def check_then_logout(stored, provided):
        ok = real_verify(stored, provided)
        if ok:
            _logout_direct(uid)
        return ok

    monkeypatch.setattr(auth, "verify_password", check_then_logout)
    token = _login_direct(EMAIL, PASSWORD)["access_token"]
    monkeypatch.setattr(auth, "verify_password", real_verify)
    assert auth.verify_access_token(token)["tv"] == 1
    assert _me(client, token).status_code == 200
    assert _token_version(uid) == 1


# ── account deletion ──────────────────────────────────────────────────────────

def test_a_user_deleted_between_the_check_and_the_lock_gets_no_token(pg, client, monkeypatch):
    """Invariant: a deleted account gets no token, even when its password was correct a moment before."""
    uid = _signup()
    real_verify = auth.verify_password

    def check_then_delete(stored, provided):
        ok = real_verify(stored, provided)
        if ok:
            database.delete_user_account(uid)
        return ok

    monkeypatch.setattr(auth, "verify_password", check_then_delete)
    with pytest.raises(HTTPException) as refused:
        _login_direct(EMAIL, PASSWORD)
    assert refused.value.status_code == 401
    monkeypatch.setattr(auth, "verify_password", real_verify)
    assert _user_count() == 0


def test_an_account_deletion_that_waits_for_a_login_leaves_no_usable_token(pg, client, pids, hold_login):
    """Invariant: a deletion that overlaps a sign-in waits for it; the token issued by the sign-in is refused
    once the account is gone."""
    uid = _signup()
    steps = [hold_login.release.set]
    passed = False
    try:
        login_thread, login = _start("login", _login_direct, EMAIL, PASSWORD)
        steps.append(lambda: _join(login_thread))
        assert hold_login.holding.wait(20), "the sign-in reached its locked block"
        login_pid = pids["login"][-1]
        delete_thread, delete = _start("delete", _delete_direct, uid)
        steps.append(lambda: _join(delete_thread))
        _wait_until(lambda: bool(pids.get("delete"))
                    and _waits_on(pids["delete"][-1], login_pid, "FROM users WHERE id"),
                    "the deletion to wait for the sign-in's lock")
        assert _user_count() == 1
        token = hold_login.issued[0]
        hold_login.release.set()
        _join(login_thread)
        _join(delete_thread)
        assert "error" not in login and "error" not in delete
        assert _user_count() == 0
        me = _me(client, token)
        assert me.status_code == 401
        assert me.json()["detail"] == USER_GONE
        passed = True
    finally:
        _cleanup(steps, passed)


# ── connections and locks after errors ────────────────────────────────────────

def test_an_error_while_issuing_the_token_releases_the_lock_and_the_connection(pg, client, pids, monkeypatch):
    """Invariant: a failure inside the locked block rolls the transaction back and returns the connection to
    the pool. With a single pool slot, the next sign-in can only succeed if the slot was given back."""
    uid = _signup()
    monkeypatch.setattr(database, "POOL_MAX", 1)
    monkeypatch.setattr(database, "POOL_TIMEOUT", 2)
    database.close_pool()  # the next use creates a pool with the single slot
    real_create = auth.create_access_token

    def broken(*args, **kwargs):
        raise RuntimeError("token issue failed")

    monkeypatch.setattr(auth, "create_access_token", broken)
    with pytest.raises(RuntimeError):
        _login_direct(EMAIL, PASSWORD)
    failed_lock_connection = pids["MainThread"][-1]
    assert _state(failed_lock_connection) == "idle", "the connection was not left in a transaction"
    assert _idle_in_transaction_elsewhere() == 0
    monkeypatch.setattr(auth, "create_access_token", real_create)
    token = _login_direct(EMAIL, PASSWORD)["access_token"]
    assert _me(client, token).status_code == 200
    assert _token_version(uid) == 0


def test_an_error_while_checking_the_password_takes_no_lock_and_keeps_no_connection(pg, pids, monkeypatch):
    """Invariant: the password check runs outside any transaction. A failure there takes no locked read."""
    _signup()
    before = len(pids.get("MainThread", []))

    def broken(stored, provided):
        raise RuntimeError("check failed")

    monkeypatch.setattr(auth, "verify_password", broken)
    with pytest.raises(RuntimeError):
        _login_direct(EMAIL, PASSWORD)
    # Only the user read took a connection; the locked read did not happen.
    assert len(pids["MainThread"]) - before == 1
    assert _idle_in_transaction_elsewhere() == 0
