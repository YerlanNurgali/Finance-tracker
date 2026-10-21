"""
Integration tests for Finance Tracker v2.

Two layers:
  Layer 1 — direct function calls through the API layer with an in-memory
             mock database. No external packages needed; always runs.
  Layer 2 — FastAPI TestClient (HTTP transport). Runs only when fastapi and
             httpx are installed (i.e. on Render/CI with requirements.txt).
             Skipped in sandbox environments where pip access is blocked.

User row tuple: (id, email, password_hash, created_at, token_version)
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

# TESTING is set per test by the autouse fixture in tests/conftest.py, which also
# undoes the database.get_connection and on_startup changes made below.

# ── package availability probes ───────────────────────────────────────────────
try:
    from fastapi.testclient import TestClient
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

_SKIP_HTTP = "fastapi+httpx not installed; run: pip install fastapi httpx"


# ── shared in-memory store ────────────────────────────────────────────────────

def _make_store():
    return {
        "users":      [],   # (id, email, password_hash, created_at, token_version)
        "settings":   {},   # {uid: {"language", "theme", "currency"}}
        "operations": [],   # (id, uid, date, op_type, amount, category)
        "budgets":    [],
        "goals":      [],
        "categories": [],   # (id, user_id, kind, name)
    }


class _Cursor:
    """Minimal in-memory cursor that mirrors real database.py queries."""

    def __init__(self, store):
        self.store = store
        self.rowcount = 0
        self.result = []

    def __enter__(self):  return self
    def __exit__(self, *a): pass

    def execute(self, query, params=()):
        q = " ".join(query.split()).upper()
        self.result = []
        self.rowcount = 0

        # ── silently ignore all DDL / migration ──────────────────────────────
        if any(k in q for k in ("CREATE TABLE", "CREATE INDEX", "DO $$",
                                 "ALTER TABLE", "ON CONFLICT")):
            return

        # ── user categories (before the generic operation branches below) ─────
        if q.startswith("SELECT ID, KIND, NAME FROM USER_CATEGORIES WHERE USER_ID"):
            self.result = [(c[0], c[2], c[3]) for c in self.store["categories"] if c[1] == params[0]]
            return
        if q.startswith("SELECT ID, KIND, NAME FROM USER_CATEGORIES WHERE ID"):
            self.result = [(c[0], c[2], c[3]) for c in self.store["categories"]
                           if c[0] == params[0] and c[1] == params[1]]
            return
        if q.startswith("INSERT INTO USER_CATEGORIES"):
            cid = max([c[0] for c in self.store["categories"]] + [0]) + 1
            self.store["categories"].append((cid, params[0], params[1], params[2]))
            self.result = [[cid]]
            self.rowcount = 1
            return
        if q.startswith("UPDATE USER_CATEGORIES SET NAME"):
            name, cid, uid = params
            self.store["categories"] = [(c[0], c[1], c[2], name) if (c[0] == cid and c[1] == uid) else c
                                        for c in self.store["categories"]]
            self.rowcount = 1
            return
        if q.startswith("DELETE FROM USER_CATEGORIES"):
            cid, uid = params
            before = len(self.store["categories"])
            self.store["categories"] = [c for c in self.store["categories"] if not (c[0] == cid and c[1] == uid)]
            self.rowcount = before - len(self.store["categories"])
            return
        if q.startswith("UPDATE OPERATIONS SET CATEGORY"):
            new, uid, kind, old = params
            rows = self.store["operations"]
            for i, o in enumerate(rows):
                if o[1] == uid and o[3] == kind and o[5] == old:
                    rows[i] = (o[0], o[1], o[2], o[3], o[4], new)
                    self.rowcount += 1
            return
        if q.startswith("UPDATE BUDGETS SET CATEGORY"):
            new, uid, old = params
            rows = self.store["budgets"]
            for i, b in enumerate(rows):
                if b[1] == uid and b[2] == old:
                    rows[i] = (b[0], b[1], new, b[3], b[4])
                    self.rowcount += 1
            return
        if q.startswith("SELECT COUNT(*) FROM BUDGETS WHERE USER_ID"):
            uid, category = params
            self.result = [(sum(1 for b in self.store["budgets"] if b[1] == uid and b[2] == category),)]
            return

        # user_settings upsert (INSERT INTO user_settings (user_id))
        if "INSERT INTO USER_SETTINGS (USER_ID)" in q:
            uid = params[0]
            if uid not in self.store["settings"]:
                self.store["settings"][uid] = {"language": "RU", "theme": "light", "currency": "KZT"}
            return

        # ── users ─────────────────────────────────────────────────────────────
        if "INSERT INTO USERS" in q:
            email, pwd = params[0], params[1]
            if any(u[1] == email for u in self.store["users"]):
                raise Exception("duplicate key value violates unique constraint")
            uid = len(self.store["users"]) + 1
            self.store["users"].append((uid, email, pwd, "2026-01-01", 0))
            self.store["settings"][uid] = {"language": "RU", "theme": "light", "currency": "KZT"}
            self.result = [[uid]]
            self.rowcount = 1
            return

        # DELETE FROM USERS check MUST come before SELECT checks —
        # a DELETE query also contains "FROM USERS" and we must not match it.
        if "DELETE FROM USERS" in q:
            uid = params[0]
            before = len(self.store["users"])
            self.store["users"] = [u for u in self.store["users"] if u[0] != uid]
            self.rowcount = before - len(self.store["users"])
            return

        if "FROM USERS WHERE EMAIL" in q:
            email = params[0]
            self.result = [u for u in self.store["users"] if u[1] == email]
            return

        if "FROM USERS WHERE ID" in q:
            uid = params[0]
            self.result = [u for u in self.store["users"] if u[0] == uid]
            return

        if "UPDATE USERS SET PASSWORD_HASH" in q:
            new_hash, uid = params[0], params[1]
            self.store["users"] = [
                (u[0], u[1], new_hash, u[3], u[4] + 1) if u[0] == uid else u
                for u in self.store["users"]
            ]
            self.rowcount = 1
            return

        if "UPDATE USERS SET TOKEN_VERSION" in q:
            uid = params[0]
            self.store["users"] = [
                (u[0], u[1], u[2], u[3], u[4] + 1) if u[0] == uid else u
                for u in self.store["users"]
            ]
            self.rowcount = 1
            return

        if "DELETE FROM USERS" in q:
            uid = params[0]
            before = len(self.store["users"])
            self.store["users"] = [u for u in self.store["users"] if u[0] != uid]
            self.rowcount = before - len(self.store["users"])
            return

        # ── user_settings ─────────────────────────────────────────────────────
        if "SELECT LANGUAGE, THEME, CURRENCY" in q and "FROM USER_SETTINGS" in q:
            uid = params[0]
            s = self.store["settings"].get(uid)
            self.result = [[s["language"], s["theme"], s["currency"],
                            s.get("budget_alerts_inapp", True), s.get("budget_alerts_push", True)]] if s else []
            return

        if "UPDATE USER_SETTINGS SET" in q:
            # database.update_user_settings builds SET ... WHERE user_id=%s
            # params are [val1, val2, ..., user_id] in the same order as fields
            uid = params[-1]
            if uid not in self.store["settings"]:
                return
            # Update settings fields directly (order matches params[:-1])
            field_names = ["language", "theme", "currency", "budget_alerts_inapp", "budget_alerts_push"]
            for i, val in enumerate(params[:-1]):
                if i < len(field_names):
                    self.store["settings"][uid][field_names[i]] = val
            self.rowcount = 1
            return

        # ── operations ────────────────────────────────────────────────────────
        if "INSERT INTO OPERATIONS" in q or (
                "INSERT INTO" in q and "OPERATION_TYPE" in q):
            oid = len(self.store["operations"]) + 1
            self.store["operations"].append(
                (oid, params[0], params[1], params[2], params[3], params[4])
            )
            self.rowcount = 1
            return

        if "SELECT ID, DATE, OPERATION_TYPE, AMOUNT, CATEGORY FROM" in q and "WHERE USER_ID" in q:
            uid = params[0]
            self.result = [
                (o[0], o[2], o[3], o[4], o[5])
                for o in self.store["operations"] if o[1] == uid
            ]
            return

        if "FROM OPERATIONS" in q and "FOR UPDATE" in q:
            oid, uid = params[0], params[1]
            self.result = [(o[3], o[5], o[2]) for o in self.store["operations"] if o[0] == oid and o[1] == uid]
            return

        if "DELETE FROM OPERATIONS" in q or ("DELETE FROM" in q and "OPERATION" in q):
            oid, uid = params[0], params[1]
            before = len(self.store["operations"])
            self.result = [(o[3], o[5], o[2]) for o in self.store["operations"] if o[0] == oid and o[1] == uid]
            self.store["operations"] = [
                o for o in self.store["operations"]
                if not (o[0] == oid and o[1] == uid)
            ]
            self.rowcount = before - len(self.store["operations"])
            return

        if "UPDATE" in q and "OPERATION_TYPE" in q:
            date, op_type, amount, category, oid, uid = params
            for i, o in enumerate(self.store["operations"]):
                if o[0] == oid and o[1] == uid:
                    self.store["operations"][i] = (oid, uid, date, op_type, amount, category)
                    self.rowcount = 1
                    return
            return

        # ── budgets ───────────────────────────────────────────────────────────
        if "INSERT INTO BUDGETS" in q:
            bid = len(self.store["budgets"]) + 1
            self.store["budgets"].append((bid, params[0], params[1], params[2], params[3]))
            self.rowcount = 1
            return

        if "SELECT ID, CATEGORY, AMOUNT, PERIOD FROM BUDGETS" in q:
            uid = params[0]
            self.result = [b for b in self.store["budgets"] if b[1] == uid]
            return

        if "DELETE FROM BUDGETS" in q:
            bid, uid = params[0], params[1]
            before = len(self.store["budgets"])
            self.store["budgets"] = [b for b in self.store["budgets"]
                                     if not (b[0] == bid and b[1] == uid)]
            self.rowcount = before - len(self.store["budgets"])
            return

        # ── goals ─────────────────────────────────────────────────────────────
        if "INSERT INTO GOALS" in q:
            gid = len(self.store["goals"]) + 1
            self.store["goals"].append((gid, params[0], params[1], params[2], params[3], params[4]))
            self.rowcount = 1
            return

        if "SELECT ID, TITLE, TARGET_AMOUNT, CURRENT_AMOUNT, DEADLINE FROM GOALS" in q:
            uid = params[0]
            self.result = [g for g in self.store["goals"] if g[1] == uid]
            return

        if "DELETE FROM GOALS" in q:
            gid, uid = params[0], params[1]
            before = len(self.store["goals"])
            self.store["goals"] = [g for g in self.store["goals"]
                                   if not (g[0] == gid and g[1] == uid)]
            self.rowcount = before - len(self.store["goals"])
            return

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return list(self.result)


class _Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):     return _Cursor(self.store)
    def commit(self):     pass
    def rollback(self):   pass
    def close(self):      pass


def _wire(store):
    """Monkey-patch database.get_connection to use the in-memory store."""
    import database
    database.get_connection = lambda: _Conn(store)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — direct function-call integration tests (no extra deps)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthFunctions(unittest.TestCase):
    """Auth module: password hashing and JWT token_version flow."""

    def test_password_round_trip(self):
        import auth
        h = auth.hash_password("MyPass123!")
        self.assertTrue(auth.verify_password(h, "MyPass123!"))
        self.assertFalse(auth.verify_password(h, "wrong"))

    def test_token_contains_tv(self):
        import auth
        token = auth.create_access_token({"user_id": 1, "email": "a@b.com", "tv": 3})
        payload = auth.verify_access_token(token)
        self.assertEqual(payload["tv"], 3)

    def test_expired_token_rejected(self):
        import auth
        from datetime import timedelta
        token = auth.create_access_token({"user_id": 1}, expires_delta=timedelta(seconds=-1))
        self.assertIsNone(auth.verify_access_token(token))


class TestDatabaseUserIsolation(unittest.TestCase):
    """Database layer: multi-user isolation via user_id filtering."""

    def setUp(self):
        self.store = _make_store()
        _wire(self.store)

    def _create_user(self, email, pwd="hash"):
        import database, auth
        h = auth.hash_password(pwd)
        return database.create_user(email, h)

    def test_operations_isolated_by_user(self):
        import database
        uid_a = self._create_user("a@x.com")
        uid_b = self._create_user("b@x.com")
        database.add_operation(uid_a, "01.10.2026, 10:00", "Доход", 5000, None)
        self.assertEqual(len(database.get_operations(uid_a)), 1)
        self.assertEqual(len(database.get_operations(uid_b)), 0)

    def test_token_version_increments_on_password_change(self):
        import database, auth
        uid = self._create_user("c@x.com", "old")
        row_before = database.get_user_by_id(uid)
        tv_before = row_before[4]
        database.update_user_password(uid, auth.hash_password("new"))
        row_after = database.get_user_by_id(uid)
        self.assertEqual(row_after[4], tv_before + 1)

    def test_delete_user_removes_all_data(self):
        import database
        uid = self._create_user("d@x.com")
        database.add_operation(uid, "01.10.2026, 10:00", "Доход", 1000, None)
        database.delete_user_account(uid)
        # In-memory store: user gone
        self.assertFalse(any(u[0] == uid for u in self.store["users"]))

    def test_duplicate_email_raises(self):
        import database
        self._create_user("dup@x.com")
        with self.assertRaises(database.UserAlreadyExists):
            self._create_user("dup@x.com")

    def test_settings_defaults(self):
        import database
        uid = self._create_user("e@x.com")
        s = database.get_user_settings(uid)
        self.assertEqual(s["language"], "RU")
        self.assertEqual(s["currency"], "KZT")

    def test_settings_update(self):
        import database
        uid = self._create_user("f@x.com")
        database.update_user_settings(uid, language="EN", theme="dark", currency="USD")
        s = database.get_user_settings(uid)
        self.assertEqual(s["language"], "EN")
        self.assertEqual(s["currency"], "USD")


class TestStatisticsFunctions(unittest.TestCase):
    """Statistics computation in API layer using mock DB."""

    def setUp(self):
        self.store = _make_store()
        _wire(self.store)
        import database, auth
        self.uid = database.create_user("stat@x.com", auth.hash_password("pw"))

    def test_empty_statistics(self):
        import database
        ops = database.get_operations(self.uid)
        income = sum(float(o[3]) for o in ops if o[2] == "Доход")
        expense = sum(float(o[3]) for o in ops if o[2] == "Расход")
        self.assertEqual(income, 0)
        self.assertEqual(expense, 0)

    def test_statistics_with_operations(self):
        import database
        database.add_operation(self.uid, "01.10.2026, 10:00", "Доход", 10000, None)
        database.add_operation(self.uid, "01.10.2026, 11:00", "Расход", 2500, "Еда")
        ops = database.get_operations(self.uid)
        income = sum(float(o[3]) for o in ops if o[2] == "Доход")
        expense = sum(float(o[3]) for o in ops if o[2] == "Расход")
        self.assertAlmostEqual(income, 10000.0)
        self.assertAlmostEqual(expense, 2500.0)
        self.assertAlmostEqual(income - expense, 7500.0)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — FastAPI TestClient HTTP tests (requires fastapi + httpx)
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class _HttpBase(unittest.TestCase):
    def setUp(self):
        import database
        import api as app_module

        self.store = _make_store()
        database.get_connection = lambda: _Conn(self.store)

        # Suppress startup hook (no real DB in tests)
        app_module.app.router.on_startup.clear()

        # Reset rate-limiter counters so one test's registrations don't push the
        # next test over the limit. The limit itself stays enforced.
        storage = getattr(app_module.limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()

        self.client = TestClient(app_module.app, raise_server_exceptions=True)

    def _register(self, email="alice@example.com", password="Secret123!"):
        return self.client.post("/auth/register", json={"email": email, "password": password})

    def _login(self, email="alice@example.com", password="Secret123!"):
        return self.client.post("/auth/login", json={"email": email, "password": password})

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpHealth(_HttpBase):
    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpAuth(_HttpBase):
    def test_register_success(self):
        r = self._register()
        self.assertEqual(r.status_code, 201)
        self.assertIn("access_token", r.json())

    def test_register_duplicate(self):
        self._register()
        self.assertEqual(self._register().status_code, 400)

    def test_register_short_password(self):
        self.assertEqual(self._register(password="abc").status_code, 422)

    def test_login_success(self):
        self._register()
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertIn("access_token", r.json())

    def test_login_wrong_password(self):
        self._register()
        self.assertEqual(self._login(password="Wrong!").status_code, 401)

    def test_get_me_authenticated(self):
        token = self._register().json()["access_token"]
        r = self.client.get("/auth/me", headers=self._headers(token))
        self.assertEqual(r.status_code, 200)
        self.assertIn("settings", r.json())

    def test_get_me_no_token(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_get_me_invalid_token(self):
        r = self.client.get("/auth/me", headers={"Authorization": "Bearer bad.token"})
        self.assertEqual(r.status_code, 401)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpRateLimits(_HttpBase):
    """POST /auth/register allows 5 and POST /auth/login 10 requests a minute per
    client address. The address is request.client.host, so X-Forwarded-For,
    which any client can set, must not open a fresh counter."""

    def setUp(self):
        super().setUp()
        import api as app_module
        # Leave no exhausted counter behind for tests in other modules.
        self.addCleanup(app_module.limiter.reset)

    @staticmethod
    def _forwarded(index):
        return {"X-Forwarded-For": f"203.0.113.{index}"}

    def test_sixth_registration_in_a_minute_is_limited(self):
        for i in range(5):
            with self.subTest(request=i + 1):
                r = self.client.post("/auth/register", json={"email": f"user{i}@example.com", "password": "Secret123!"})
                self.assertNotEqual(r.status_code, 429)
                self.assertEqual(r.status_code, 201)
        r = self.client.post("/auth/register", json={"email": "user5@example.com", "password": "Secret123!"})
        self.assertEqual(r.status_code, 429)

    def test_x_forwarded_for_does_not_bypass_the_registration_limit(self):
        # Every request claims a different address; they still share one counter.
        for i in range(5):
            with self.subTest(request=i + 1):
                r = self.client.post("/auth/register", headers=self._forwarded(i),
                                     json={"email": f"fwd{i}@example.com", "password": "Secret123!"})
                self.assertEqual(r.status_code, 201)
        for i in range(5, 9):
            with self.subTest(request=i + 1):
                r = self.client.post("/auth/register", headers=self._forwarded(i),
                                     json={"email": f"fwd{i}@example.com", "password": "Secret123!"})
                self.assertEqual(r.status_code, 429)
        self.assertEqual(self.store["users"][-1][1], "fwd4@example.com", "no limited request created a user")

    def test_eleventh_login_in_a_minute_is_limited(self):
        self.assertEqual(self._register().status_code, 201)
        for i in range(10):
            with self.subTest(request=i + 1):
                r = self._login()
                self.assertNotEqual(r.status_code, 429)
                self.assertEqual(r.status_code, 200)
        self.assertEqual(self._login().status_code, 429)

    def test_x_forwarded_for_does_not_bypass_the_login_limit(self):
        self.assertEqual(self._register().status_code, 201)
        credentials = {"email": "alice@example.com", "password": "Secret123!"}
        for i in range(10):
            with self.subTest(request=i + 1):
                r = self.client.post("/auth/login", headers=self._forwarded(i), json=credentials)
                self.assertEqual(r.status_code, 200)
        for i in range(10, 14):
            with self.subTest(request=i + 1):
                r = self.client.post("/auth/login", headers=self._forwarded(i), json=credentials)
                self.assertEqual(r.status_code, 429)
                self.assertNotIn("access_token", r.text)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpTokenVersion(_HttpBase):
    def test_old_token_rejected_after_password_change(self):
        token = self._register().json()["access_token"]
        h = self._headers(token)
        # Works before
        self.assertEqual(self.client.get("/auth/me", headers=h).status_code, 200)
        # Change password
        r = self.client.put("/auth/password",
                            json={"current_password": "Secret123!", "new_password": "NewPwd456!"},
                            headers=h)
        self.assertEqual(r.status_code, 200)
        # Old token rejected
        self.assertEqual(self.client.get("/auth/me", headers=h).status_code, 401)

    def test_password_change_returns_a_token_for_this_session(self):
        old_h = self._headers(self._register().json()["access_token"])
        r = self.client.put("/auth/password",
                            json={"current_password": "Secret123!", "new_password": "NewPwd456!"},
                            headers=old_h)
        self.assertEqual(r.status_code, 200)
        new_h = self._headers(r.json()["access_token"])
        self.assertEqual(self.client.get("/auth/me", headers=new_h).status_code, 200)
        self.assertEqual(self.client.get("/auth/me", headers=old_h).status_code, 401)

    def test_wrong_current_password_returns_no_token(self):
        h = self._headers(self._register().json()["access_token"])
        r = self.client.put("/auth/password",
                            json={"current_password": "wrong", "new_password": "NewPwd456!"},
                            headers=h)
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("access_token", r.text)
        self.assertEqual(self.client.get("/auth/me", headers=h).status_code, 200)

    def test_new_token_valid_after_password_change(self):
        self._register()
        old_h = self._headers(self._login().json()["access_token"])
        self.client.put("/auth/password",
                        json={"current_password": "Secret123!", "new_password": "NewPwd456!"},
                        headers=old_h)
        new_token = self._login(password="NewPwd456!").json()["access_token"]
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(new_token)).status_code, 200)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpLogoutAndLimits(_HttpBase):
    """POST /auth/logout ends every session of the account. Password change and account deletion
    are limited per account, and login is also limited per account, whatever address it comes from."""

    def setUp(self):
        super().setUp()
        import api as app_module
        self.addCleanup(app_module.limiter.reset)

    def _change_password(self, token, current="Secret123!"):
        return self.client.put("/auth/password",
                               json={"current_password": current, "new_password": "NewPwd456!"},
                               headers=self._headers(token))

    def _delete(self, token, password):
        return self.client.request("DELETE", "/auth/delete", json={"password": password},
                                   headers=self._headers(token))

    def test_logout_ends_every_session_of_the_account(self):
        first = self._register().json()["access_token"]
        second = self._login().json()["access_token"]
        r = self.client.post("/auth/logout", headers=self._headers(first))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(first)).status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(second)).status_code, 401)

    def test_login_after_logout_gives_a_working_session(self):
        token = self._register().json()["access_token"]
        self.client.post("/auth/logout", headers=self._headers(token))
        new = self._login()
        self.assertEqual(new.status_code, 200)
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(new.json()["access_token"])).status_code, 200)

    def test_logout_needs_a_valid_token(self):
        self.assertEqual(self.client.post("/auth/logout").status_code, 401)
        bad = self.client.post("/auth/logout", headers={"Authorization": "Bearer bad.token"})
        self.assertEqual(bad.status_code, 401)

    def test_logout_does_not_end_another_accounts_sessions(self):
        alice = self._register().json()["access_token"]
        bob = self._register(email="bob@example.com").json()["access_token"]
        self.client.post("/auth/logout", headers=self._headers(alice))
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(bob)).status_code, 200)

    def test_a_revoked_token_is_rejected_by_every_checked_route(self):
        token = self._register().json()["access_token"]
        self.client.post("/auth/logout", headers=self._headers(token))
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(token)).status_code, 401)
        self.assertEqual(self._change_password(token).status_code, 401)
        self.assertEqual(self._delete(token, "Secret123!").status_code, 401)
        self.assertEqual(self.client.post("/auth/logout", headers=self._headers(token)).status_code, 401)

    def test_password_change_ends_sessions_on_other_devices(self):
        here = self._register().json()["access_token"]
        elsewhere = self._login().json()["access_token"]
        self.assertEqual(self._change_password(here).status_code, 200)
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(elsewhere)).status_code, 401)

    def test_password_change_is_limited_per_account(self):
        token = self._register().json()["access_token"]
        for i in range(5):
            with self.subTest(attempt=i + 1):
                self.assertEqual(self._change_password(token, current="wrong").status_code, 401)
        self.assertEqual(self._change_password(token, current="wrong").status_code, 429)
        self.assertEqual(self._change_password(token).status_code, 429,
                         "the correct password must not get past the limit")

    def test_password_limit_does_not_block_another_account(self):
        alice = self._register().json()["access_token"]
        bob = self._register(email="bob@example.com").json()["access_token"]
        for _ in range(6):
            self._change_password(alice, current="wrong")
        self.assertEqual(self._change_password(bob, current="wrong").status_code, 401)

    def test_account_deletion_is_limited_per_account(self):
        token = self._register().json()["access_token"]
        for i in range(5):
            with self.subTest(attempt=i + 1):
                self.assertEqual(self._delete(token, "wrong").status_code, 401)
        self.assertEqual(self._delete(token, "wrong").status_code, 429)
        self.assertEqual(self._delete(token, "Secret123!").status_code, 429,
                         "the correct password must not get past the limit")
        self.assertEqual(len(self.store["users"]), 1, "the account must still exist")

    def test_login_is_limited_per_account_whatever_the_address(self):
        from unittest import mock
        import api as app_module

        self.assertEqual(self._register().status_code, 201)
        credentials = {"email": "alice@example.com", "password": "Secret123!"}
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            # A new address on every attempt: only the per-account limit can refuse the eleventh.
            for i in range(10):
                with self.subTest(attempt=i + 1):
                    r = self.client.post("/auth/login", json=credentials,
                                         headers={"X-Forwarded-For": f"203.0.113.{i}"})
                    self.assertEqual(r.status_code, 200)
            r = self.client.post("/auth/login", json=credentials, headers={"X-Forwarded-For": "203.0.113.10"})
            self.assertEqual(r.status_code, 429)
            self.assertNotIn("access_token", r.text)
            other = self.client.post("/auth/login", headers={"X-Forwarded-For": "203.0.113.11"},
                                     json={"email": "bob@example.com", "password": "Secret123!"})
            self.assertEqual(other.status_code, 401, "another account must not be refused by this limit")


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpOperations(_HttpBase):
    def setUp(self):
        super().setUp()
        self.token = self._register().json()["access_token"]
        self.h = self._headers(self.token)

    def _op(self, **kw):
        p = {"date": "01.10.2026, 10:00", "operation_type": "Доход", "amount": 5000.0, "category": None}
        p.update(kw)
        return self.client.post("/operations", json=p, headers=self.h)

    def test_create_income(self):
        self.assertEqual(self._op().status_code, 201)

    def test_create_expense_no_category_rejected(self):
        self.assertEqual(self._op(operation_type="Расход", amount=500, category=None).status_code, 422)

    def test_create_expense_with_category(self):
        self.assertEqual(self._op(operation_type="Расход", amount=500, category="Еда").status_code, 201)

    def test_list_operations(self):
        self._op(amount=1000)
        self._op(amount=2000)
        r = self.client.get("/operations", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 2)

    def test_unauthenticated_returns_401(self):
        self.assertEqual(self.client.get("/operations").status_code, 401)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpUserIsolation(_HttpBase):
    def test_users_cannot_see_each_others_operations(self):
        t_a = self._register("a@x.com", "PwdA123!").json()["access_token"]
        t_b = self._register("b@x.com", "PwdB123!").json()["access_token"]
        # Alice adds an operation
        self.client.post("/operations",
                         json={"date": "01.10.2026, 10:00", "operation_type": "Доход", "amount": 9999},
                         headers=self._headers(t_a))
        # Bob sees nothing
        r = self.client.get("/operations", headers=self._headers(t_b))
        self.assertEqual(len(r.json()), 0)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpStatistics(_HttpBase):
    def setUp(self):
        super().setUp()
        self.h = self._headers(self._register().json()["access_token"])

    def test_statistics_empty(self):
        r = self.client.get("/statistics", headers=self.h)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["total_income"], 0)
        self.assertIn("monthly_trend", data)

    def test_statistics_after_operations(self):
        self.client.post("/operations",
                         json={"date": "01.10.2026, 10:00", "operation_type": "Доход", "amount": 10000},
                         headers=self.h)
        self.client.post("/operations",
                         json={"date": "01.10.2026, 11:00", "operation_type": "Расход",
                               "amount": 2500, "category": "Еда"},
                         headers=self.h)
        data = self.client.get("/statistics", headers=self.h).json()
        self.assertAlmostEqual(data["total_income"], 10000.0)
        self.assertAlmostEqual(data["total_expense"], 2500.0)
        self.assertAlmostEqual(data["balance"], 7500.0)


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpOperationDates(_HttpBase):
    """The date of a new or edited operation is validated by the server; rows that are
    already stored keep their text. The in-memory store holds (id, user, date, type, amount, category)."""

    STORED = "%d.%m.%Y, %H:%M:%S"

    def setUp(self):
        super().setUp()
        self.h = self._headers(self._register().json()["access_token"])

    def _payload(self, date, **kw):
        payload = {"date": date, "operation_type": "Доход", "amount": 100.0, "category": None}
        payload.update(kw)
        return payload

    def _post(self, date, **kw):
        return self.client.post("/operations", json=self._payload(date, **kw), headers=self.h)

    def _put(self, operation_id, date, **kw):
        return self.client.put(f"/operations/{operation_id}", json=self._payload(date, **kw), headers=self.h)

    def _dates(self):
        return [row[2] for row in self.store["operations"]]

    def _ago(self, days):
        return (datetime.now() - timedelta(days=days)).strftime(self.STORED)

    def _ahead(self, days):
        return (datetime.now() + timedelta(days=days)).strftime(self.STORED)

    def test_a_chosen_date_is_stored_as_sent_and_listed(self):
        self.assertEqual(self._post("05.10.2026, 14:30:00").status_code, 201)
        self.assertEqual(self._dates(), ["05.10.2026, 14:30:00"])
        listed = self.client.get("/operations", headers=self.h).json()
        self.assertEqual([item["date"] for item in listed], ["05.10.2026, 14:30:00"])

    def test_a_date_without_a_time_is_stored_as_midnight(self):
        self.assertEqual(self._post("05.10.2026").status_code, 201)
        self.assertEqual(self._dates(), ["05.10.2026, 00:00:00"])

    def test_an_expense_of_an_earlier_month_is_saved_without_an_alert_and_counted_in_its_month(self):
        date = self._ago(90)
        response = self._post(date, operation_type="Расход", amount=300, category="Еда")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["alerts"], [])
        self.assertEqual(self._dates(), [date])
        trend = self.client.get("/statistics", headers=self.h).json()["monthly_trend"]
        self.assertEqual(trend[date[6:10] + "-" + date[3:5]]["expense"], 300.0)

    def test_an_unreadable_date_is_refused_and_nothing_is_stored(self):
        for date in ("not a date", "31.02.2026, 10:00:00", "", "2026-10-16 10:00", "16.10.2026, 24:00:00"):
            with self.subTest(date=date):
                response = self._post(date)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self._dates(), [])
        self.assertEqual(self._post("not a date").json()["detail"], "Некорректная дата операции")

    def test_a_date_too_far_ahead_or_too_old_is_refused(self):
        response = self._post(self._ahead(40))
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (422, "Дата операции не может быть позже, чем через 31 день"))
        response = self._post("31.12.1999, 23:59:59")
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (422, "Дата операции не может быть раньше 01.01.2000"))
        self.assertEqual(self._dates(), [])

    def test_a_date_within_the_limit_ahead_is_accepted(self):
        self.assertEqual(self._post(self._ahead(30)).status_code, 201)
        self.assertEqual(len(self._dates()), 1)

    def test_editing_changes_the_date(self):
        self._post("05.10.2026, 14:30:00")
        response = self._put(1, "06.10.2026, 09:00:00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._dates(), ["06.10.2026, 09:00:00"])

    def test_editing_with_an_unreadable_date_changes_nothing(self):
        self._post("05.10.2026, 14:30:00")
        for date in ("garbage", self._ahead(40), "01.01.1999, 00:00:00"):
            with self.subTest(date=date):
                self.assertEqual(self._put(1, date, amount=777).status_code, 422)
        self.assertEqual(self.store["operations"][0][2:5], ("05.10.2026, 14:30:00", "Доход", 100.0))

    def test_rows_stored_earlier_keep_their_text_and_stay_listed(self):
        legacy = ["16.10.2026 10:00", "16.10.2026, 10:00", "2026-10-16T10:00", "2026-10-16", "16.10.2026", "someday"]
        for index, date in enumerate(legacy, start=1):
            self.store["operations"].append((index, 1, date, "Доход", 10.0, None))
        listed = self.client.get("/operations", headers=self.h)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["date"] for item in listed.json()], legacy)
        self.assertEqual(self.client.get("/statistics", headers=self.h).status_code, 200)
        self.assertEqual(self._dates(), legacy)

    def test_a_row_with_a_readable_old_date_can_be_edited_with_that_date_unchanged(self):
        self.store["operations"].append((1, 1, "16.10.2026 10:00", "Доход", 10.0, None))
        self.assertEqual(self._put(1, "16.10.2026 10:00", amount=25).status_code, 200)
        self.assertEqual(self.store["operations"][0][2:5], ("16.10.2026 10:00", "Доход", 25.0))

    def test_another_users_operation_cannot_be_edited_and_keeps_its_date(self):
        self._post("05.10.2026, 14:30:00")
        other = self._headers(self._register("b@x.com", "PwdB123!").json()["access_token"])
        response = self.client.put("/operations/1", json=self._payload("06.10.2026, 09:00:00"), headers=other)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._dates(), ["05.10.2026, 14:30:00"])

    def test_the_owner_comes_from_the_token_not_from_the_body(self):
        body = self._payload("05.10.2026, 14:30:00")
        body["user_id"] = 999
        self.assertEqual(self.client.post("/operations", json=body, headers=self.h).status_code, 201)
        self.assertEqual([row[1] for row in self.store["operations"]], [1])

    def test_the_date_is_still_refused_without_a_token(self):
        response = self.client.post("/operations", json=self._payload("not a date"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self._dates(), [])


@unittest.skipUnless(FASTAPI_AVAILABLE, _SKIP_HTTP)
class TestHttpCategories(_HttpBase):
    """User categories through the real sign-in (tokens), on the in-memory store. The
    store holds operations as (id, user, date, type, amount, category) and budgets as
    (id, user, category, amount, period). tests/test_categories.py covers the rules in detail."""

    def setUp(self):
        super().setUp()
        self.alice = self._headers(self._register("alice@x.com", "PwdA123!").json()["access_token"])
        self.bob = self._headers(self._register("bob@x.com", "PwdB123!").json()["access_token"])

    def _create(self, headers, name="Хобби", kind="Расход"):
        return self.client.post("/categories", json={"kind": kind, "name": name}, headers=headers)

    def _operation(self, headers, category, kind="Расход"):
        return self.client.post("/operations", headers=headers, json={
            "date": "01.10.2026, 10:00:00", "operation_type": kind, "amount": 100, "category": category})

    def _own(self, headers):
        body = self.client.get("/categories", headers=headers).json()
        return [c for c in body["categories"] if not c["builtin"]]

    def test_create_list_rename_and_delete(self):
        created = self._create(self.alice)
        self.assertEqual(created.status_code, 201)
        category = created.json()["category"]
        self.assertEqual(self._own(self.alice), [category])
        self._operation(self.alice, "Хобби")
        renamed = self.client.put(f"/categories/{category['id']}", json={"name": "Увлечения"}, headers=self.alice)
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["operations_updated"], 1)
        listed = self.client.get("/operations", headers=self.alice).json()
        self.assertEqual([item["category"] for item in listed], ["Увлечения"])
        deleted = self.client.delete(f"/categories/{category['id']}", headers=self.alice)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual((deleted.json()["operations_moved"], deleted.json()["moved_to"]), (1, "Другое"))
        listed = self.client.get("/operations", headers=self.alice).json()
        self.assertEqual([item["category"] for item in listed], ["Другое"], "the history is kept")
        self.assertEqual(self._own(self.alice), [])

    def test_a_user_never_sees_or_changes_another_users_categories(self):
        category = self._create(self.alice).json()["category"]
        self._operation(self.alice, "Хобби")
        self.assertEqual(self._own(self.bob), [])
        renamed = self.client.put(f"/categories/{category['id']}", json={"name": "Чужое"}, headers=self.bob)
        deleted = self.client.delete(f"/categories/{category['id']}", headers=self.bob)
        self.assertEqual((renamed.status_code, deleted.status_code), (404, 404))
        self.assertEqual(self._own(self.alice), [category])
        self.assertEqual(self.store["operations"][0][5], "Хобби")
        self.assertEqual(self._create(self.bob).status_code, 201, "the same name is free for another user")

    def test_the_owner_comes_from_the_token_not_from_the_body(self):
        response = self.client.post("/categories", headers=self.alice,
                                    json={"kind": "Расход", "name": "Хобби", "user_id": 2})
        self.assertEqual(response.status_code, 201)
        self.assertEqual([(c[1], c[3]) for c in self.store["categories"]], [(1, "Хобби")])

    def test_a_category_with_a_budget_is_not_deleted(self):
        category = self._create(self.alice).json()["category"]
        self.client.post("/budgets", headers=self.alice, json={"category": "Хобби", "amount": 500})
        response = self.client.delete(f"/categories/{category['id']}", headers=self.alice)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._own(self.alice), [category])

    def test_bad_names_and_duplicates_are_refused(self):
        self.assertEqual(self._create(self.alice).status_code, 201)
        self.assertEqual(self._create(self.alice, "хобби").status_code, 409)
        self.assertEqual(self._create(self.alice, "Food").status_code, 422)
        self.assertEqual(self._create(self.alice, "   ").status_code, 422)
        self.assertEqual(self._create(self.alice, "Хобби", kind="Перевод").status_code, 422)
        self.assertEqual(len(self.store["categories"]), 1)

    def test_every_endpoint_needs_a_token(self):
        self.assertEqual(self.client.get("/categories").status_code, 401)
        self.assertEqual(self.client.post("/categories", json={"kind": "Расход", "name": "Хобби"}).status_code, 401)
        self.assertEqual(self.client.put("/categories/1", json={"name": "Новая"}).status_code, 401)
        self.assertEqual(self.client.delete("/categories/1").status_code, 401)
        self.assertEqual(self.store["categories"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
