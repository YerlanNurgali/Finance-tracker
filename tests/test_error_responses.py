"""Internal errors (database driver, configuration) must never reach the client,
while expected validation and business messages keep reaching it unchanged.

No database is used: the data layer is replaced with functions that raise.
"""
import pytest

import api
import database

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

# What a psycopg2 error or a misconfiguration would put into str(exc).
SECRET = 'connection to server at "ep-secret-host.neon.tech" (10.0.0.7), port 5432 failed: password authentication failed for user "owner"'
INTERNAL_ERRORS = [
    RuntimeError(SECRET),
    ValueError("DATABASE_URL не настроен"),                      # database.get_connection()
    ValueError("A string literal cannot contain NUL (0x00) characters."),  # psycopg2 parameter check
]
LEAK_MARKERS = ("ep-secret-host", "password authentication", "DATABASE_URL", "NUL (0x00)", "Traceback")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    api.limiter.reset()
    yield TestClient(api.app, raise_server_exceptions=False)
    api.limiter.reset()


@pytest.fixture
def signed_in(client, monkeypatch):
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user,
                        lambda: {"id": 1, "email": "user@example.test"})
    monkeypatch.setattr(database, "get_budgets", lambda user_id: [])
    monkeypatch.setattr(database, "get_operations", lambda user_id: [])
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: {"currency": "KZT"})
    return client


def raising(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def assert_no_leak(response):
    for marker in LEAK_MARKERS:
        assert marker not in response.text, f"{marker!r} leaked in {response.status_code} {response.text!r}"


OPERATION = {"date": "01.10.2026, 10:00:00", "operation_type": "Расход", "amount": 10, "category": "Еда"}
BUDGET = {"category": "Еда", "amount": 100}
GOAL = {"title": "Отпуск", "target_amount": 1000, "current_amount": 0}

# (method, path, json body, database function the endpoint calls)
WRITES = [
    ("POST", "/operations", OPERATION, "add_operation"),
    ("PUT", "/operations/1", OPERATION, "update_operation"),
    ("POST", "/budgets", BUDGET, "add_budget"),
    ("PUT", "/budgets/1", BUDGET, "update_budget"),
    ("POST", "/goals", GOAL, "add_goal"),
    ("PUT", "/goals/1/progress", {"current_amount": 5}, "update_goal_progress"),
    ("PUT", "/auth/profile", {"currency": "USD"}, "update_user_settings"),
]


@pytest.mark.parametrize("error", INTERNAL_ERRORS, ids=["driver", "config", "nul"])
@pytest.mark.parametrize("method,path,body,function", WRITES, ids=[w[1] + " " + w[0] for w in WRITES])
def test_internal_errors_become_a_plain_500(signed_in, monkeypatch, method, path, body, function, error):
    monkeypatch.setattr(database, function, raising(error))
    response = signed_in.request(method, path, json=body)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert_no_leak(response)


@pytest.mark.parametrize("error", INTERNAL_ERRORS, ids=["driver", "config", "nul"])
def test_registration_does_not_echo_database_errors(client, monkeypatch, error):
    monkeypatch.setattr(database, "create_user", raising(error))
    response = client.post("/auth/register", json={"email": "new@example.test", "password": "secret12"})
    assert response.status_code == 500
    assert_no_leak(response)


@pytest.mark.parametrize("error", INTERNAL_ERRORS, ids=["driver", "config", "nul"])
def test_csv_import_commit_does_not_echo_database_errors(signed_in, monkeypatch, error):
    monkeypatch.setattr(database, "import_operations", raising(error))
    response = signed_in.post("/import/csv/commit?date_column=0&amount_column=1",
                              content="Дата;Сумма\n15.01.2026;-100\n".encode(), headers={"Content-Type": "text/csv"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Не удалось сохранить импорт. Данные не изменены"
    assert_no_leak(response)


# --- Expected errors keep their messages -------------------------------------------

@pytest.mark.parametrize("method,path,body,function", [w for w in WRITES if w[3] != "update_user_settings"],
                         ids=[w[1] + " " + w[0] for w in WRITES if w[3] != "update_user_settings"])
def test_validation_messages_still_reach_the_client(signed_in, monkeypatch, method, path, body, function):
    monkeypatch.setattr(database, function, raising(database.InvalidInput("Сумма должна быть больше нуля")))
    response = signed_in.request(method, path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "Сумма должна быть больше нуля"


@pytest.mark.parametrize("method,path,function,error,message", [
    ("DELETE", "/operations/9", "delete_operation", database.OperationNotFound, "Операция не найдена"),
    ("DELETE", "/budgets/9", "delete_budget", database.BudgetNotFound, "Бюджет не найден"),
    ("DELETE", "/goals/9", "delete_goal", database.GoalNotFound, "Цель не найдена"),
])
def test_not_found_messages_still_reach_the_client(signed_in, monkeypatch, method, path, function, error, message):
    monkeypatch.setattr(database, function, raising(error(message)))
    response = signed_in.request(method, path)
    assert response.status_code == 404
    assert response.json()["detail"] == message


def test_duplicate_registration_keeps_its_message(client, monkeypatch):
    monkeypatch.setattr(database, "create_user",
                        raising(database.UserAlreadyExists("Пользователь с таким email уже существует")))
    response = client.post("/auth/register", json={"email": "taken@example.test", "password": "secret12"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Пользователь с таким email уже существует"


def test_the_data_layer_reports_bad_input_as_invalid_input():
    for check in (lambda: database._validate_operation("Расход", 0, "Еда"),
                  lambda: database._validate_operation("Перевод", 10, "Еда"),
                  lambda: database._validate_operation("Расход", 10, ""),
                  lambda: database._validate_operation("Доход", "abc", None)):
        with pytest.raises(database.InvalidInput):
            check()
    assert issubclass(database.InvalidInput, ValueError), "existing ValueError handlers keep working"


@pytest.mark.parametrize("get_budgets", [
    raising(RuntimeError(SECRET)),
    lambda user_id: [(1, "Еда", "not-a-number", "month")],
], ids=["database", "bad-row"])
def test_a_failing_budget_check_still_reports_the_saved_operation(signed_in, monkeypatch, caplog, get_budgets):
    # An error here would make the user save the same operation again.
    saved = []
    monkeypatch.setattr(database, "add_operation", lambda *args: saved.append(args))
    monkeypatch.setattr(database, "get_budgets", get_budgets)
    response = signed_in.post("/operations", json=OPERATION)
    assert len(saved) == 1
    assert response.status_code == 201
    assert response.json() == {"message": "Операция добавлена", "notification": None}
    assert "Budget check failed" in caplog.text
    for marker in LEAK_MARKERS + ("not-a-number", "could not convert"):
        assert marker not in response.text and marker not in caplog.text


def test_an_exceeded_budget_still_warns(signed_in, monkeypatch):
    monkeypatch.setattr(database, "add_operation", lambda *args: None)
    monkeypatch.setattr(database, "get_budgets", lambda user_id: [(1, "Еда", 5, "month")])
    monkeypatch.setattr(api, "_rows", lambda user_id, period: [(1, "01.10.2026", "Расход", 10, "Еда")])
    response = signed_in.post("/operations", json=OPERATION)
    assert response.status_code == 201
    assert response.json()["notification"] == "⚠️ Бюджет на Еда превышен!"


# --- Input PostgreSQL cannot store is rejected before reaching it ------------------

# (method, path, body with a NUL character, database function the endpoint calls)
NUL_INPUTS = [
    ("POST", "/operations", {**OPERATION, "category": "Е\x00да"}, "add_operation"),
    ("POST", "/operations", {**OPERATION, "date": "01.10.2026\x00"}, "add_operation"),
    ("PUT", "/operations/1", {**OPERATION, "category": "Е\x00да"}, "update_operation"),
    ("POST", "/budgets", {**BUDGET, "category": "Е\x00да"}, "add_budget"),
    ("PUT", "/budgets/1", {**BUDGET, "period": "month\x00"}, "update_budget"),
    ("POST", "/goals", {**GOAL, "title": "От\x00пуск"}, "add_goal"),
    ("POST", "/goals", {**GOAL, "deadline": "2027-01-01\x00"}, "add_goal"),
]


def _nul_field(body):
    return next(key for key, value in body.items() if "\x00" in str(value))


@pytest.mark.parametrize("method,path,body,function", NUL_INPUTS,
                         ids=[f"{m} {p} {_nul_field(b)}" for m, p, b, _ in NUL_INPUTS])
def test_text_with_nul_is_invalid_input(signed_in, monkeypatch, method, path, body, function):
    monkeypatch.setattr(database, function, raising(AssertionError("the data layer must not be reached")))
    response = signed_in.request(method, path, json=body)
    assert response.status_code == 422
    assert "недопустимый символ" in response.text


@pytest.mark.parametrize("path,function", [("/auth/register", "create_user"), ("/auth/login", "get_user_by_email")])
def test_an_email_with_nul_is_invalid_input(client, monkeypatch, path, function):
    monkeypatch.setattr(database, function, raising(AssertionError("the data layer must not be reached")))
    response = client.post(path, json={"email": "a\x00b@example.test", "password": "secret12"})
    assert response.status_code == 422


def test_passwords_are_not_checked_for_nul(client, monkeypatch):
    # Passwords are only hashed, so the rule does not change who can sign in.
    monkeypatch.setattr(database, "get_user_by_email", lambda email: None)
    response = client.post("/auth/login", json={"email": "user@example.test", "password": "se\x00cret"})
    assert response.status_code == 401


# Python's JSON parser accepts these tokens; NUMERIC(14, 2) cannot store them.
NON_FINITE = ["Infinity", "-Infinity", "NaN", "1e400"]
AMOUNT_BODIES = [
    ("POST", "/operations", '{"date": "01.10.2026", "operation_type": "Доход", "amount": %s}', "add_operation"),
    ("PUT", "/operations/1", '{"date": "01.10.2026", "operation_type": "Доход", "amount": %s}', "update_operation"),
    ("POST", "/budgets", '{"category": "Еда", "amount": %s}', "add_budget"),
    ("PUT", "/budgets/1", '{"category": "Еда", "amount": %s}', "update_budget"),
    ("POST", "/goals", '{"title": "Отпуск", "target_amount": %s}', "add_goal"),
    ("POST", "/goals", '{"title": "Отпуск", "target_amount": 100, "current_amount": %s}', "add_goal"),
    ("PUT", "/goals/1/progress", '{"current_amount": %s}', "update_goal_progress"),
]


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("method,path,template,function", AMOUNT_BODIES,
                         ids=[f"{m} {p} {i}" for i, (m, p, _, _) in enumerate(AMOUNT_BODIES)])
def test_non_finite_amounts_are_invalid_input(signed_in, monkeypatch, method, path, template, function, value):
    monkeypatch.setattr(database, function, raising(AssertionError("the data layer must not be reached")))
    response = signed_in.request(method, path, content=(template % value).encode(),
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] in {"amount", "target_amount", "current_amount"}


def test_the_validation_response_keeps_fastapis_format(signed_in):
    response = signed_in.post("/budgets", json={"category": "Еда", "amount": -5})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": [{
        "type": "greater_than", "loc": ["body", "amount"], "msg": "Input should be greater than 0",
        "input": -5, "ctx": {"gt": 0},
    }]}


def test_a_non_finite_value_is_echoed_as_text(signed_in):
    response = signed_in.post("/budgets", content=b'{"category": "Food", "amount": NaN}',
                              headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "nan"
