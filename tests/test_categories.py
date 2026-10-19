"""User categories: the data layer and the /categories endpoints.

No database is used. database.get_connection is replaced by an in-memory stand-in that
understands exactly the statements the category code sends (any other statement fails the
test) and undoes everything when a transaction is rolled back. It checks the Python logic
and the shape of the SQL; it cannot prove the SQL itself is right: that needs a database.
"""
import copy
import types

import pytest

import database

pytest.importorskip("httpx")
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

EXPENSE, INCOME = "Расход", "Доход"
OTHER = "Другое"
HOSTILE = "'); DROP TABLE operations; --"  # hostile input: it must only ever travel as a parameter


class FakeDatabase:
    def __init__(self):
        self.users = {1, 2}
        # categories: [id, user_id, kind, name]; operations: [id, user_id, type, category];
        # budgets: [id, user_id, category]
        self.data = {"categories": [], "operations": [], "budgets": [], "next_id": 1}
        self.statements = []  # (normalised SQL text, parameters) in the order they were sent
        self.fail_on = None   # a text; a statement containing it fails


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rowcount = 0
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=()):
        text = " ".join(query.split())
        self.db.statements.append((text, params))
        q = text.upper()
        if self.db.fail_on and self.db.fail_on in q:
            raise RuntimeError("database failure")
        data = self.db.data
        self.rowcount = 0
        self.result = []
        if q.startswith("SELECT ID FROM USERS WHERE ID = %S"):
            self.result = [(params[0],)] if params[0] in self.db.users else []
        elif q.startswith("SELECT ID, KIND, NAME FROM USER_CATEGORIES WHERE USER_ID = %S"):
            self.result = [(c[0], c[2], c[3]) for c in data["categories"] if c[1] == params[0]]
        elif q.startswith("SELECT ID, KIND, NAME FROM USER_CATEGORIES WHERE ID = %S AND USER_ID = %S"):
            self.result = [(c[0], c[2], c[3]) for c in data["categories"] if c[0] == params[0] and c[1] == params[1]]
        elif q.startswith("INSERT INTO USER_CATEGORIES (USER_ID, KIND, NAME) VALUES (%S, %S, %S) RETURNING ID"):
            new_id = data["next_id"]
            data["next_id"] += 1
            data["categories"].append([new_id, params[0], params[1], params[2]])
            self.result = [(new_id,)]
            self.rowcount = 1
        elif q.startswith("UPDATE USER_CATEGORIES SET NAME = %S WHERE ID = %S AND USER_ID = %S"):
            for c in data["categories"]:
                if c[0] == params[1] and c[1] == params[2]:
                    c[3] = params[0]
                    self.rowcount += 1
        elif q.startswith("UPDATE OPERATIONS SET CATEGORY = %S WHERE USER_ID = %S AND OPERATION_TYPE = %S AND CATEGORY = %S"):
            for o in data["operations"]:
                if o[1] == params[1] and o[2] == params[2] and o[3] == params[3]:
                    o[3] = params[0]
                    self.rowcount += 1
        elif q.startswith("UPDATE BUDGETS SET CATEGORY = %S WHERE USER_ID = %S AND CATEGORY = %S"):
            for b in data["budgets"]:
                if b[1] == params[1] and b[2] == params[2]:
                    b[2] = params[0]
                    self.rowcount += 1
        elif q.startswith("SELECT COUNT(*) FROM BUDGETS WHERE USER_ID = %S AND CATEGORY = %S"):
            self.result = [(sum(1 for b in data["budgets"] if b[1] == params[0] and b[2] == params[1]),)]
        elif q.startswith("DELETE FROM USER_CATEGORIES WHERE ID = %S AND USER_ID = %S"):
            before = len(data["categories"])
            data["categories"] = [c for c in data["categories"] if not (c[0] == params[0] and c[1] == params[1])]
            self.rowcount = before - len(data["categories"])
        else:
            raise AssertionError("unexpected statement: " + text)

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return list(self.result)


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.snapshot = copy.deepcopy(db.data)

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        self.db.data = copy.deepcopy(self.snapshot)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDatabase()
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection(fake))
    return fake


@pytest.fixture
def env(db, monkeypatch):
    """A signed-in client (user 1 unless ``as_user`` says otherwise) and the stand-in database."""
    monkeypatch.setattr(api.app.router, "on_startup", [])
    state = types.SimpleNamespace(user=1, sync_calls=[])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user,
                        lambda: {"id": state.user, "email": "user@example.test"})
    monkeypatch.setattr(database, "evaluate_budget_alerts",
                        lambda user_id, targets: state.sync_calls.append((user_id, list(targets))) or [])
    state.db = db
    state.client = TestClient(api.app, raise_server_exceptions=False)
    return state


def add_category(db, user, kind, name):
    new_id = db.data["next_id"]
    db.data["next_id"] += 1
    db.data["categories"].append([new_id, user, kind, name])
    return new_id


def add_operation(db, user, kind, category):
    db.data["operations"].append([len(db.data["operations"]) + 1, user, kind, category])


def add_budget(db, user, category):
    db.data["budgets"].append([len(db.data["budgets"]) + 1, user, category])


def categories_of(db, user):
    return [(c[2], c[3]) for c in db.data["categories"] if c[1] == user]


def operation_categories(db, user, kind):
    return [o[3] for o in db.data["operations"] if o[1] == user and o[2] == kind]


# --- Listing -----------------------------------------------------------------------------

def test_the_list_has_the_built_in_categories_then_the_users_own(env):
    first = env.client.get("/categories")
    assert first.status_code == 200
    body = first.json()
    assert body["limit"] == database.MAX_USER_CATEGORIES == 50
    built_in = [(c["kind"], c["name"]) for c in body["categories"]]
    assert built_in == [(kind, name) for kind, names in database.BUILTIN_CATEGORIES.items() for name in names]
    assert all(c["builtin"] is True and c["id"] is None for c in body["categories"])
    assert (EXPENSE, "Еда") in built_in and (INCOME, "Зарплата") in built_in and built_in.count((EXPENSE, OTHER)) == 1

    created = env.client.post("/categories", json={"kind": EXPENSE, "name": "Хобби"}).json()["category"]
    listed = env.client.get("/categories").json()["categories"]
    assert listed[-1] == {"id": created["id"], "kind": EXPENSE, "name": "Хобби", "builtin": False}
    assert len(listed) == len(built_in) + 1


def test_the_built_in_categories_are_the_ones_the_interface_offers():
    # Mirrors the lists in frontend/script.js; kept in step by hand, so this fails when only one changes.
    assert database.BUILTIN_CATEGORIES[EXPENSE] == ("Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", OTHER)
    assert database.BUILTIN_CATEGORIES[INCOME] == ("Зарплата", "Фриланс", "Бизнес", "Инвестиции", OTHER)


# --- Creating ----------------------------------------------------------------------------

def test_a_category_is_created_trimmed_and_owned_by_the_token_user(env):
    response = env.client.post("/categories", json={"kind": EXPENSE, "name": "  Хобби  ", "user_id": 2})
    assert response.status_code == 201
    category = response.json()["category"]
    assert category == {"id": 1, "kind": EXPENSE, "name": "Хобби", "builtin": False}
    assert response.json()["message"] == "Категория добавлена"
    assert env.db.data["categories"] == [[1, 1, EXPENSE, "Хобби"]], "the owner is the signed-in user, not the body's user_id"


def test_the_same_name_may_exist_for_both_kinds_but_not_twice_for_one(env):
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Подарки"}).status_code == 201
    assert env.client.post("/categories", json={"kind": INCOME, "name": "Подарки"}).status_code == 201
    for name in ("Подарки", "подарки", "ПОДАРКИ", "  Подарки "):
        response = env.client.post("/categories", json={"kind": EXPENSE, "name": name})
        assert (response.status_code, response.json()["detail"]) == (409, database.CATEGORY_EXISTS_MESSAGE), name
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Games"}).status_code == 201
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "gAMES"}).status_code == 409
    assert len(env.db.data["categories"]) == 3


def test_another_user_may_use_the_same_name(env):
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Хобби"}).status_code == 201
    env.user = 2
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Хобби"}).status_code == 201
    assert categories_of(env.db, 1) == categories_of(env.db, 2) == [(EXPENSE, "Хобби")]


@pytest.mark.parametrize("name,message", [
    ("   ", database.CATEGORY_NAME_LENGTH_MESSAGE),
    ("x" * 51, database.CATEGORY_NAME_LENGTH_MESSAGE),
    ("a\nb", database.CATEGORY_NAME_CHARACTERS_MESSAGE),
    ("a\tb", database.CATEGORY_NAME_CHARACTERS_MESSAGE),
    ("Еда", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("еДа", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Food", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("FOOD", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Тамақ", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Other", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Басқа", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Salary", database.CATEGORY_NAME_BUILTIN_MESSAGE),
    ("Ойын-сауық", database.CATEGORY_NAME_BUILTIN_MESSAGE),
])
@pytest.mark.parametrize("kind", [EXPENSE, INCOME])
def test_a_bad_name_is_refused_and_nothing_is_stored(env, kind, name, message):
    response = env.client.post("/categories", json={"kind": kind, "name": name})
    assert (response.status_code, response.json()["detail"]) == (422, message)
    assert env.db.data["categories"] == []


def test_a_name_of_50_characters_is_accepted_and_one_of_51_is_not(env):
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "я" * 50}).status_code == 201
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "я" * 51}).status_code == 422


def test_invalid_input_is_refused_by_the_payload_check(env):
    bodies = [
        {"kind": "Перевод", "name": "Хобби"},
        {"kind": "расход", "name": "Хобби"},
        {"kind": EXPENSE},
        {"name": "Хобби"},
        {"kind": EXPENSE, "name": ""},
        {"kind": EXPENSE, "name": None},
        {"kind": EXPENSE, "name": 5},
        {"kind": EXPENSE, "name": "Е\x00да"},
        {"kind": EXPENSE, "name": "x" * 201},
        {"kind": ["Расход"], "name": "Хобби"},
    ]
    for body in bodies:
        assert env.client.post("/categories", json=body).status_code == 422, body
    assert env.db.data["categories"] == []


def test_a_nul_character_is_refused_by_the_data_layer_too(db):
    with pytest.raises(database.InvalidInput):
        database.add_user_category(1, EXPENSE, "Е\x00да")
    assert db.data["categories"] == []


def test_the_limit_is_50_categories_per_user(env):
    for index in range(49):
        add_category(env.db, 1, EXPENSE if index % 2 else INCOME, f"Своя {index}")
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Пятидесятая"}).status_code == 201
    response = env.client.post("/categories", json={"kind": EXPENSE, "name": "Лишняя"})
    assert (response.status_code, response.json()["detail"]) == (422, database.CATEGORY_LIMIT_MESSAGE)
    assert len(categories_of(env.db, 1)) == 50
    env.user = 2
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": "Лишняя"}).status_code == 201, "the limit is per user"


def test_the_names_are_never_part_of_the_sql_text(env):
    assert env.client.post("/categories", json={"kind": EXPENSE, "name": HOSTILE}).status_code == 201
    category_id = env.db.data["categories"][0][0]
    add_operation(env.db, 1, EXPENSE, HOSTILE)
    assert env.client.put(f"/categories/{category_id}", json={"name": HOSTILE + " 2"}).status_code == 200
    assert env.client.delete(f"/categories/{category_id}", params={"move_to": HOSTILE}).status_code == 422
    assert env.client.delete(f"/categories/{category_id}").status_code == 200
    assert env.db.statements
    for text, params in env.db.statements:
        assert "DROP" not in text.upper() and "--" not in text and HOSTILE not in text
        assert text.count("%s") == len(params), text


def test_every_statement_on_the_users_data_is_limited_to_the_user(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    env.client.post("/categories", json={"kind": EXPENSE, "name": "Игры"})
    env.client.get("/categories")
    env.client.put(f"/categories/{category_id}", json={"name": "Увлечения"})
    env.client.delete(f"/categories/{category_id}")
    # The row lock on the user (SELECT id FROM users ...) is the only statement without a user_id filter.
    touched = [text for text, _ in env.db.statements if not text.startswith("SELECT id FROM users WHERE id = %s")]
    assert touched, "statements were recorded"
    for text in touched:
        assert "user_id = %s" in text or "(user_id, kind, name)" in text, text


# --- Renaming ----------------------------------------------------------------------------

def test_renaming_updates_the_category_the_operations_and_the_budgets_of_that_user_only(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    for _ in range(2):
        add_operation(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, INCOME, "Хобби")          # the income category of the same name
    add_operation(env.db, 1, EXPENSE, "Еда")
    add_operation(env.db, 2, EXPENSE, "Хобби")         # another user's
    add_budget(env.db, 1, "Хобби")
    add_budget(env.db, 2, "Хобби")
    response = env.client.put(f"/categories/{category_id}", json={"name": " Увлечения "})
    assert response.status_code == 200
    assert response.json() == {
        "message": "Категория переименована",
        "category": {"id": category_id, "kind": EXPENSE, "name": "Увлечения", "builtin": False},
        "operations_updated": 2, "budgets_updated": 1,
    }
    assert categories_of(env.db, 1) == [(EXPENSE, "Увлечения")]
    assert operation_categories(env.db, 1, EXPENSE) == ["Увлечения", "Увлечения", "Еда"]
    assert operation_categories(env.db, 1, INCOME) == ["Хобби"]
    assert operation_categories(env.db, 2, EXPENSE) == ["Хобби"]
    assert [b[2] for b in env.db.data["budgets"] if b[1] == 1] == ["Увлечения"]
    assert [b[2] for b in env.db.data["budgets"] if b[1] == 2] == ["Хобби"]


def test_renaming_an_income_category_leaves_the_budgets_alone(env):
    category_id = add_category(env.db, 1, INCOME, "Хобби")
    add_operation(env.db, 1, INCOME, "Хобби")
    add_budget(env.db, 1, "Хобби")
    body = env.client.put(f"/categories/{category_id}", json={"name": "Подработка"}).json()
    assert (body["operations_updated"], body["budgets_updated"]) == (1, 0)
    assert [b[2] for b in env.db.data["budgets"]] == ["Хобби"]


def test_a_change_of_letter_case_is_a_rename_and_the_same_name_changes_nothing(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    same = env.client.put(f"/categories/{category_id}", json={"name": "Хобби"}).json()
    assert (same["operations_updated"], same["budgets_updated"]) == (0, 0)
    changed = env.client.put(f"/categories/{category_id}", json={"name": "ХОББИ"})
    assert changed.status_code == 200 and changed.json()["operations_updated"] == 1
    assert operation_categories(env.db, 1, EXPENSE) == ["ХОББИ"]


def test_renaming_to_a_taken_or_built_in_name_is_refused_and_changes_nothing(env):
    first = add_category(env.db, 1, EXPENSE, "Хобби")
    add_category(env.db, 1, EXPENSE, "Игры")
    add_category(env.db, 1, INCOME, "Подарки")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    taken = env.client.put(f"/categories/{first}", json={"name": "игры"})
    assert (taken.status_code, taken.json()["detail"]) == (409, database.CATEGORY_EXISTS_MESSAGE)
    built_in = env.client.put(f"/categories/{first}", json={"name": "Food"})
    assert (built_in.status_code, built_in.json()["detail"]) == (422, database.CATEGORY_NAME_BUILTIN_MESSAGE)
    assert env.client.put(f"/categories/{first}", json={"name": "   "}).status_code == 422
    assert env.client.put(f"/categories/{first}", json={"name": "Подарки"}).status_code == 200, "the same name of the other kind is free"
    assert operation_categories(env.db, 1, EXPENSE) == ["Подарки"]


def test_renaming_a_missing_or_foreign_id_is_not_found(env):
    add_category(env.db, 1, EXPENSE, "Хобби")
    theirs = add_category(env.db, 2, EXPENSE, "Хобби")
    add_operation(env.db, 2, EXPENSE, "Хобби")
    for category_id in (theirs, 999, 0, -5):
        response = env.client.put(f"/categories/{category_id}", json={"name": "Новая"})
        assert (response.status_code, response.json()["detail"]) == (404, database.CATEGORY_NOT_FOUND_MESSAGE), category_id
    assert categories_of(env.db, 2) == [(EXPENSE, "Хобби")] and operation_categories(env.db, 2, EXPENSE) == ["Хобби"]
    assert categories_of(env.db, 1) == [(EXPENSE, "Хобби")]


def test_an_id_the_column_cannot_hold_is_not_found_without_touching_the_database(db):
    for category_id in (0, -1, 2 ** 63, 10 ** 30, True, "1", None):
        with pytest.raises(database.CategoryNotFound):
            database.rename_user_category(category_id, 1, "Новая")
        with pytest.raises(database.CategoryNotFound):
            database.delete_user_category(category_id, 1)
    assert db.statements == []


def test_a_built_in_category_has_no_id_to_rename_or_delete(env):
    for category_id in ("null", "Еда", "1.5"):
        assert env.client.put(f"/categories/{category_id}", json={"name": "Новая"}).status_code == 422
        assert env.client.delete(f"/categories/{category_id}").status_code == 422


def test_a_rename_that_fails_half_way_changes_nothing(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    add_budget(env.db, 1, "Хобби")
    env.db.fail_on = "UPDATE BUDGETS"
    assert env.client.put(f"/categories/{category_id}", json={"name": "Увлечения"}).status_code == 500
    assert categories_of(env.db, 1) == [(EXPENSE, "Хобби")]
    assert operation_categories(env.db, 1, EXPENSE) == ["Хобби"]
    assert [b[2] for b in env.db.data["budgets"]] == ["Хобби"]


# --- Deleting ----------------------------------------------------------------------------

def test_deleting_an_unused_category(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    response = env.client.delete(f"/categories/{category_id}")
    assert response.status_code == 200
    assert response.json() == {"message": "Категория удалена", "kind": EXPENSE, "name": "Хобби",
                               "moved_to": OTHER, "operations_moved": 0}
    assert env.db.data["categories"] == []
    assert env.sync_calls == [], "nothing moved, no budget level to follow"


def test_deleting_moves_the_operations_of_that_user_and_kind_to_other_and_keeps_the_history(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_category(env.db, 1, INCOME, "Хобби")
    for _ in range(3):
        add_operation(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, INCOME, "Хобби")
    add_operation(env.db, 2, EXPENSE, "Хобби")
    add_budget(env.db, 2, "Хобби")                     # another user's budget does not block
    response = env.client.delete(f"/categories/{category_id}")
    assert response.status_code == 200
    assert (response.json()["operations_moved"], response.json()["moved_to"]) == (3, OTHER)
    assert operation_categories(env.db, 1, EXPENSE) == [OTHER] * 3
    assert operation_categories(env.db, 1, INCOME) == ["Хобби"]
    assert operation_categories(env.db, 2, EXPENSE) == ["Хобби"]
    assert len(env.db.data["operations"]) == 5, "no operation is lost"
    assert categories_of(env.db, 1) == [(INCOME, "Хобби")]


def test_the_budget_levels_of_the_target_follow_silently_after_a_move(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    env.client.delete(f"/categories/{category_id}")
    assert env.sync_calls
    user_id, targets = env.sync_calls[0]
    assert user_id == 1 and targets and all(category == OTHER and notify is False for category, _, notify in targets)


def test_moving_income_operations_does_not_touch_budget_levels(env):
    category_id = add_category(env.db, 1, INCOME, "Подарки")
    add_operation(env.db, 1, INCOME, "Подарки")
    assert env.client.delete(f"/categories/{category_id}").json()["operations_moved"] == 1
    assert env.sync_calls == []


@pytest.mark.parametrize("move_to,expected", [
    ("Еда", "Еда"), ("food", "Еда"), ("ТАМАҚ", "Еда"), (" Дом ", "Дом"), ("other", OTHER), ("Басқа", OTHER),
    ("игры", "Игры"), ("", OTHER),
])
def test_operations_can_be_moved_to_a_chosen_category(env, move_to, expected):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_category(env.db, 1, EXPENSE, "Игры")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    response = env.client.delete(f"/categories/{category_id}", params={"move_to": move_to})
    assert response.status_code == 200 and response.json()["moved_to"] == expected
    assert operation_categories(env.db, 1, EXPENSE) == [expected]


@pytest.mark.parametrize("move_to,message", [
    ("Хобби", database.CATEGORY_MOVE_SAME_MESSAGE),
    ("хобби", database.CATEGORY_MOVE_SAME_MESSAGE),
    ("Неизвестная", database.CATEGORY_MOVE_NOT_FOUND_MESSAGE),
    ("Зарплата", database.CATEGORY_MOVE_NOT_FOUND_MESSAGE),   # an income category for an expense one
    ("Подарки", database.CATEGORY_MOVE_NOT_FOUND_MESSAGE),    # the user's income category
    ("Чужая", database.CATEGORY_MOVE_NOT_FOUND_MESSAGE),      # another user's category
])
def test_a_bad_target_is_refused_and_nothing_is_deleted_or_moved(env, move_to, message):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_category(env.db, 1, INCOME, "Подарки")
    add_category(env.db, 2, EXPENSE, "Чужая")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    response = env.client.delete(f"/categories/{category_id}", params={"move_to": move_to})
    assert (response.status_code, response.json()["detail"]) == (422, message)
    assert (EXPENSE, "Хобби") in categories_of(env.db, 1)
    assert operation_categories(env.db, 1, EXPENSE) == ["Хобби"]


def test_an_expense_category_with_a_budget_is_not_deleted(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    add_budget(env.db, 1, "Хобби")
    response = env.client.delete(f"/categories/{category_id}")
    assert (response.status_code, response.json()["detail"]) == (409, database.CATEGORY_IN_USE_MESSAGE)
    assert categories_of(env.db, 1) == [(EXPENSE, "Хобби")]
    assert operation_categories(env.db, 1, EXPENSE) == ["Хобби"]
    env.db.data["budgets"].clear()
    assert env.client.delete(f"/categories/{category_id}").status_code == 200


def test_an_income_category_is_not_blocked_by_a_budget_of_the_same_name(env):
    category_id = add_category(env.db, 1, INCOME, "Хобби")
    add_budget(env.db, 1, "Хобби")
    assert env.client.delete(f"/categories/{category_id}").status_code == 200


def test_deleting_a_missing_or_foreign_category_is_not_found(env):
    theirs = add_category(env.db, 2, EXPENSE, "Хобби")
    add_operation(env.db, 2, EXPENSE, "Хобби")
    for category_id in (theirs, 999, 0, -1):
        response = env.client.delete(f"/categories/{category_id}")
        assert (response.status_code, response.json()["detail"]) == (404, database.CATEGORY_NOT_FOUND_MESSAGE), category_id
    assert categories_of(env.db, 2) == [(EXPENSE, "Хобби")]
    assert operation_categories(env.db, 2, EXPENSE) == ["Хобби"]


def test_a_delete_that_fails_half_way_moves_nothing(env):
    category_id = add_category(env.db, 1, EXPENSE, "Хобби")
    add_operation(env.db, 1, EXPENSE, "Хобби")
    env.db.fail_on = "DELETE FROM USER_CATEGORIES"
    assert env.client.delete(f"/categories/{category_id}").status_code == 500
    assert categories_of(env.db, 1) == [(EXPENSE, "Хобби")]
    assert operation_categories(env.db, 1, EXPENSE) == ["Хобби"]
    assert env.sync_calls == []


# --- Isolation and access ----------------------------------------------------------------

def test_a_user_sees_only_their_own_categories(env):
    add_category(env.db, 1, EXPENSE, "Только первого")
    add_category(env.db, 2, EXPENSE, "Только второго")
    names = lambda: [c["name"] for c in env.client.get("/categories").json()["categories"] if not c["builtin"]]  # noqa: E731
    assert names() == ["Только первого"]
    env.user = 2
    assert names() == ["Только второго"]


def test_every_endpoint_needs_a_signed_in_user(monkeypatch, db):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.get("/categories").status_code == 401
    assert client.post("/categories", json={"kind": EXPENSE, "name": "Хобби"}).status_code == 401
    assert client.put("/categories/1", json={"name": "Новая"}).status_code == 401
    assert client.delete("/categories/1").status_code == 401
    assert db.statements == [], "nothing reached the database"


# --- The table ---------------------------------------------------------------------------

class RecordingCursor:
    def __init__(self, statements):
        self.statements = statements
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=()):
        self.statements.append(" ".join(query.split()).upper())

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self, statements):
        self.statements = statements

    def cursor(self):
        return RecordingCursor(self.statements)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_the_table_is_only_added_by_create_database_and_nothing_existing_changes(monkeypatch):
    statements = []
    monkeypatch.setattr(database, "get_connection", lambda: RecordingConnection(statements))
    database.create_database()
    mine = [text for text in statements if "USER_CATEGORIES" in text]
    assert len(mine) == 2
    assert mine[0].startswith("CREATE TABLE IF NOT EXISTS USER_CATEGORIES (")
    assert "ID BIGSERIAL PRIMARY KEY" in mine[0] and "USER_ID BIGINT NOT NULL REFERENCES USERS(ID) ON DELETE CASCADE" in mine[0]
    assert "KIND TEXT NOT NULL CHECK (KIND IN ('ДОХОД', 'РАСХОД'))" in mine[0] and "NAME TEXT NOT NULL" in mine[0]
    assert mine[1].startswith("CREATE UNIQUE INDEX IF NOT EXISTS") and "(USER_ID, KIND, LOWER(NAME))" in mine[1]
    assert not any(text.startswith(("DELETE", "UPDATE ")) or "DROP " in text for text in statements)
    assert not any(text.startswith("ALTER TABLE") and "USER_CATEGORIES" in text for text in statements)


def test_category_items_lists_the_built_in_ones_first():
    items = database.category_items([(7, EXPENSE, "Хобби"), (8, INCOME, "Подарки")])
    assert items[:11] == database.category_items([])
    assert items[-2:] == [{"id": 7, "kind": EXPENSE, "name": "Хобби", "builtin": False},
                          {"id": 8, "kind": INCOME, "name": "Подарки", "builtin": False}]
