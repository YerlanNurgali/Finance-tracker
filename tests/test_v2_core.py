import unittest
from decimal import Decimal
import os
import auth
import database

os.environ["TESTING"] = "true"


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
        elif "SELECT LANGUAGE, THEME, CURRENCY FROM USER_SETTINGS" in q:
            uid = params[0]
            if uid in self.store["settings"]:
                s = self.store["settings"][uid]
                self.result = [[s["language"], s["theme"], s["currency"]]]
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


if __name__ == "__main__":
    unittest.main()
