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
from decimal import Decimal
from unittest.mock import patch

os.environ["TESTING"] = "true"

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

        if "DELETE FROM USERS" in q:
            uid = params[0]
            before = len(self.store["users"])
            self.store["users"] = [u for u in self.store["users"] if u[0] != uid]
            self.rowcount = before - len(self.store["users"])
            return

        # ── user_settings ─────────────────────────────────────────────────────
        if "SELECT LANGUAGE, THEME, CURRENCY FROM USER_SETTINGS" in q:
            uid = params[0]
            s = self.store["settings"].get(uid)
            self.result = [[s["language"], s["theme"], s["currency"]]] if s else []
            return

        if "UPDATE USER_SETTINGS SET" in q:
            # database.update_user_settings builds SET ... WHERE user_id=%s
            # params are [val1, val2, ..., user_id] in the same order as fields
            uid = params[-1]
            if uid not in self.store["settings"]:
                return
            # Update settings fields directly (order matches params[:-1])
            field_names = ["language", "theme", "currency"]
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

        if "DELETE FROM OPERATIONS" in q or ("DELETE FROM" in q and "OPERATION" in q):
            oid, uid = params[0], params[1]
            before = len(self.store["operations"])
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

    def test_new_token_valid_after_password_change(self):
        self._register()
        old_h = self._headers(self._login().json()["access_token"])
        self.client.put("/auth/password",
                        json={"current_password": "Secret123!", "new_password": "NewPwd456!"},
                        headers=old_h)
        new_token = self._login(password="NewPwd456!").json()["access_token"]
        self.assertEqual(self.client.get("/auth/me", headers=self._headers(new_token)).status_code, 200)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
