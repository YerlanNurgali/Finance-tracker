import unittest
from datetime import datetime, timedelta
from decimal import Decimal
import os
import auth
import budget_alerts
import database

try:
    import api
except ImportError:  # fastapi and its dependencies are not installed
    api = None

# TESTING is set per test by the autouse fixture in tests/conftest.py, which also
# undoes the database.get_connection change made in setUp.


class MockCursor:
    def __init__(self, store):
        self.store = store
        self.rowcount = 0
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=()):
        q = " ".join(query.split()).upper()
        if "CREATE TABLE" in q or "DO $$" in q:
            return
        if "INSERT INTO USERS" in q:
            uid = len(self.store["users"]) + 1
            self.store["users"].append((uid, params[0], params[1]))
            # insert default settings
            self.store["settings"][uid] = {"language": "RU", "theme": "light", "currency": "KZT"}
            self.result = [[uid]]
            self.rowcount = 1
        elif "SELECT ID, EMAIL, PASSWORD_HASH" in q:
            for u in self.store["users"]:
                if u[1] == params[0] or u[0] == params[0]:
                    self.result = [u]
                    return
            self.result = []
        elif "SELECT LANGUAGE, THEME, CURRENCY" in q and "FROM USER_SETTINGS" in q:
            uid = params[0]
            if uid in self.store["settings"]:
                s = self.store["settings"][uid]
                self.result = [[s["language"], s["theme"], s["currency"],
                                s.get("budget_alerts_inapp", True), s.get("budget_alerts_push", True)]]
            else:
                self.result = []
        elif "UPDATE USER_SETTINGS" in q:
            uid = params[-1]
            if uid in self.store["settings"]:
                if "LANGUAGE" in q:
                    self.store["settings"][uid]["language"] = params[0]
                if "THEME" in q:
                    self.store["settings"][uid]["theme"] = params[1]
                if "CURRENCY" in q:
                    self.store["settings"][uid]["currency"] = params[2]
            self.rowcount = 1
        elif "DELETE FROM USERS" in q:
            uid = params[0]
            before = len(self.store["users"])
            self.store["users"] = [u for u in self.store["users"] if u[0] != uid]
            self.rowcount = int(len(self.store["users"]) != before)
        elif "INSERT INTO OPERATIONS" in q:
            self.store["operations"].append((len(self.store["operations"]) + 1, params[0], params[1], params[2], params[3], params[4]))
            self.rowcount = 1
        elif "SELECT ID, DATE, OPERATION_TYPE, AMOUNT, CATEGORY FROM OPERATIONS" in q:
            uid = params[0]
            self.store["operations"] = [o for o in self.store["users"] if False] # simplified
            self.result = []
        elif "DELETE FROM OPERATIONS" in q:
            self.store["operations"] = []
            self.rowcount = 1

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


class MockConnection:
    def __init__(self, store):
        self.store = store
        self.closed = 0

    def cursor(self):
        return MockCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


class TestAuthAndSecurity(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        password = "SecurePassword123!"
        hashed = auth.hash_password(password)
        self.assertTrue(auth.verify_password(hashed, password))
        self.assertFalse(auth.verify_password(hashed, "WrongPassword"))

    def test_jwt_tokens(self):
        payload = {"user_id": 42, "email": "test@example.com"}
        token = auth.create_access_token(payload)
        verified = auth.verify_access_token(token)
        self.assertEqual(verified["user_id"], 42)


class TestUserSettingsAndSecurity(unittest.TestCase):
    def setUp(self):
        self.store = {"users": [], "settings": {}, "operations": []}
        database.get_connection = lambda: MockConnection(self.store)
        self.user_id = database.create_user("test@example.com", "hashed_pwd")

    def test_settings_flow(self):
        settings = database.get_user_settings(self.user_id)
        self.assertEqual(settings["language"], "RU")
        self.assertEqual(settings["theme"], "light")
        self.assertEqual(settings["currency"], "KZT")

        database.update_user_settings(self.user_id, language="EN", theme="dark", currency="USD")
        settings = database.get_user_settings(self.user_id)
        self.assertEqual(settings["language"], "EN")
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["currency"], "USD")

    def test_password_change_flow(self):
        new_hash = auth.hash_password("NewPassword123!")
        database.update_user_password(self.user_id, new_hash)
        self.assertTrue(auth.verify_password(new_hash, "NewPassword123!"))

    def test_account_deletion_flow(self):
        database.delete_user_account(self.user_id)
        self.assertEqual(len(self.store["users"]), 0)


@unittest.skipIf(api is None, "fastapi is not installed")
class TestOperationDateText(unittest.TestCase):
    """api.checked_operation_date: what a new or edited operation may carry as its date.
    The clock is passed in, so none of these depends on today's date."""

    NOW = datetime(2026, 10, 18, 12, 0, 0)
    STORED = "%d.%m.%Y, %H:%M:%S"

    def check(self, value, now=None):
        return api.checked_operation_date(value, now or self.NOW)

    def refused(self, value, message, now=None):
        with self.assertRaises(api.HTTPException) as raised:
            self.check(value, now)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail, message)

    def test_the_format_of_the_frontend_and_the_import_is_kept_as_sent(self):
        self.assertEqual(self.check("18.10.2026, 09:05:30"), "18.10.2026, 09:05:30")

    def test_every_format_the_budgets_read_is_accepted_and_stored_unchanged(self):
        for date_format in budget_alerts.DATE_FORMATS:
            self.assertIn(date_format, api.OPERATION_DATE_FORMATS)
        for value in ("16.10.2026 10:00", "16.10.2026, 10:00", "16.10.2026, 10:00:00", "2026-10-16T10:00", "2026-10-16"):
            with self.subTest(value=value):
                self.assertEqual(self.check(value), value)
                self.assertEqual(budget_alerts.month_key(self.check(value)), "2026-10")

    def test_a_date_without_a_time_becomes_midnight_so_the_budgets_can_read_it(self):
        self.assertIsNone(budget_alerts.month_key("16.10.2026"))
        stored = self.check("16.10.2026")
        self.assertEqual(stored, "16.10.2026, 00:00:00")
        self.assertEqual(budget_alerts.month_key(stored), "2026-10")

    def test_surrounding_spaces_are_trimmed_and_nothing_else_changes(self):
        self.assertEqual(self.check("  16.10.2026, 10:00  "), "16.10.2026, 10:00")

    def test_an_unreadable_date_is_refused_and_never_replaced_by_the_current_one(self):
        for value in ("", "   ", "not a date", "31.02.2026, 10:00:00", "16.10.2026, 24:00:00",
                      "16.10.2026, 10:60:00", "2026-13-01", "16/10/2026", "2026-10-16 10:00", None, 20261016):
            with self.subTest(value=value):
                self.refused(value, api.DATE_INVALID)

    def test_the_earliest_date_is_the_first_moment_of_2000(self):
        self.assertEqual(self.check("01.01.2000, 00:00:00"), "01.01.2000, 00:00:00")
        self.refused("31.12.1999, 23:59:59", api.DATE_TOO_OLD)
        self.refused("01.01.0001, 00:00:00", api.DATE_TOO_OLD)

    def test_the_latest_date_is_31_days_after_the_servers_clock(self):
        latest = self.NOW + timedelta(days=31)
        self.assertEqual(self.check(latest.strftime(self.STORED)), latest.strftime(self.STORED))
        self.refused((latest + timedelta(minutes=1)).strftime(self.STORED), api.DATE_TOO_FAR)
        self.refused("18.10.2027, 12:00:00", api.DATE_TOO_FAR)

    def test_the_messages_name_the_limits_they_enforce(self):
        self.assertEqual(api.MIN_OPERATION_DATE, datetime(2000, 1, 1))
        self.assertEqual(api.MAX_OPERATION_DATE_AHEAD, timedelta(days=31))
        self.assertIn("01.01.2000", api.DATE_TOO_OLD)
        self.assertIn("31", api.DATE_TOO_FAR)


if __name__ == "__main__":
    unittest.main()
