"""Each user sees, changes and deletes only their own balance, statistics, goals, budgets and
exports, through the HTTP API, on the PostgreSQL sandbox.

The database is reached only through the ``pg_database_url`` fixture (pg_gate, pg_sandbox):
without FT_TEST_POSTGRES_URL these tests are skipped, and tests/db_safety.py refuses every
other connection. Each cross-user test also checks the owner's data afterwards, not only the
status code.
"""
import csv
import io

import pytest

import database
from db_safety import sandbox_configured, sandbox_sql as sql

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]
pytest.importorskip("psycopg2")
pytest.importorskip("fastapi")

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OCT = "16.10.2026, 10:00:00"
PASSWORD = "secret12"


@pytest.fixture
def pg(pg_database_url):
    """Fresh application tables built by create_database()."""
    database.close_pool()
    sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
        "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    database.create_database()
    yield
    database.close_pool()


@pytest.fixture
def http(pg, monkeypatch):
    """The app over the migrated database; no second startup."""
    monkeypatch.setattr(api.app.router, "on_startup", [])
    api.limiter.reset()
    yield TestClient(api.app)
    api.limiter.reset()


def sign_up(client, email):
    api.limiter.reset()
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add_operation(client, headers, operation_type, amount, category, date=OCT):
    response = client.post("/operations", headers=headers, json={
        "date": date, "operation_type": operation_type, "amount": amount, "category": category})
    assert response.status_code == 201, response.text
    return response


def goal_id(client, headers, title):
    return next(goal["id"] for goal in client.get("/goals", headers=headers).json() if goal["title"] == title)


def budget_id(client, headers, category):
    return next(budget["id"] for budget in client.get("/budgets", headers=headers).json()
                if budget["category"] == category)


def seed_export_data(client):
    """Alice: an expense and an income. Bob: one expense with other names and amounts."""
    alice = sign_up(client, "alice@example.test")
    bob = sign_up(client, "bob@example.test")
    add_operation(client, alice, "Расход", 111, "Аренда")
    add_operation(client, alice, "Доход", 222, "Зарплата")
    add_operation(client, bob, "Расход", 33, "Кафе")
    return alice, bob


@pytest.fixture
def drawn_pdf(monkeypatch):
    """Records the text the PDF export passes to reportlab, on the real canvas (same method as
    the export tests in test_export.py)."""
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    drawn = []
    original = {name: getattr(canvas.Canvas, name) for name in ("drawString", "drawRightString")}

    def record(name):
        def method(self, *args, **kwargs):
            drawn.append(args[2])
            return original[name](self, *args, **kwargs)
        return method

    for name in original:
        monkeypatch.setattr(canvas.Canvas, name, record(name))
    return drawn


# --- Balance and statistics -------------------------------------------------------------

def test_balance_is_per_user(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    add_operation(http, alice, "Доход", 100, "Зарплата")
    add_operation(http, alice, "Расход", 30, "Еда")
    add_operation(http, bob, "Расход", 5, "Еда")

    response = http.get("/balance", headers=alice)
    assert response.status_code == 200
    assert response.json() == {"balance": 70}
    assert http.get("/balance", headers=bob).json() == {"balance": -5}
    # Bob's requests changed nothing of Alice's rows.
    assert http.get("/balance", headers=alice).json() == {"balance": 70}
    assert len(http.get("/operations", headers=alice).json()) == 2


def test_statistics_are_per_user(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    add_operation(http, alice, "Доход", 100, "Зарплата")
    add_operation(http, alice, "Расход", 30, "Еда")
    add_operation(http, bob, "Расход", 5, "Кафе")

    alice_stats = http.get("/statistics?period=all", headers=alice)
    assert alice_stats.status_code == 200
    body = alice_stats.json()
    assert (body["total_income"], body["total_expense"], body["balance"], body["operations_count"]) == (100, 30, 70, 2)
    assert body["categories"] == {"Еда": 30}

    bob_stats = http.get("/statistics?period=all", headers=bob)
    assert bob_stats.status_code == 200
    body = bob_stats.json()
    assert (body["total_income"], body["total_expense"], body["operations_count"]) == (0, 5, 1)
    assert body["categories"] == {"Кафе": 5}

    # Alice's totals are the same after Bob's request.
    again = http.get("/statistics?period=all", headers=alice).json()
    assert (again["total_income"], again["total_expense"], again["operations_count"]) == (100, 30, 2)


# --- Goals --------------------------------------------------------------------------------

def test_goals_are_listed_per_user(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    response = http.post("/goals", headers=alice, json={"title": "Отпуск", "target_amount": 500})
    assert response.status_code == 201

    assert http.get("/goals", headers=bob).json() == []
    alice_goals = http.get("/goals", headers=alice).json()
    assert [(goal["title"], goal["current_amount"]) for goal in alice_goals] == [("Отпуск", 0)]


def test_another_users_goal_cannot_change_progress(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    http.post("/goals", headers=alice, json={"title": "Отпуск", "target_amount": 500})
    gid = goal_id(http, alice, "Отпуск")

    response = http.put(f"/goals/{gid}/progress", headers=bob, json={"current_amount": 999})
    assert response.status_code == 404
    assert response.json()["detail"] == "Цель не найдена"
    goal = http.get("/goals", headers=alice).json()[0]
    assert (goal["id"], goal["current_amount"]) == (gid, 0)


def test_another_users_goal_cannot_be_deleted(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    http.post("/goals", headers=alice, json={"title": "Отпуск", "target_amount": 500})
    gid = goal_id(http, alice, "Отпуск")

    response = http.delete(f"/goals/{gid}", headers=bob)
    assert response.status_code == 404
    assert response.json()["detail"] == "Цель не найдена"
    assert [goal["id"] for goal in http.get("/goals", headers=alice).json()] == [gid]


def test_owner_can_change_and_delete_own_goal(http):
    alice = sign_up(http, "alice@example.test")
    http.post("/goals", headers=alice, json={"title": "Отпуск", "target_amount": 500})
    gid = goal_id(http, alice, "Отпуск")

    response = http.put(f"/goals/{gid}/progress", headers=alice, json={"current_amount": 150})
    assert response.status_code == 200
    assert http.get("/goals", headers=alice).json()[0]["current_amount"] == 150

    assert http.delete(f"/goals/{gid}", headers=alice).status_code == 200
    assert http.get("/goals", headers=alice).json() == []


# --- Budgets ------------------------------------------------------------------------------

def test_another_users_budget_cannot_be_changed(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    assert http.post("/budgets", headers=alice, json={"category": "Еда", "amount": 1000}).status_code == 201
    bid = budget_id(http, alice, "Еда")

    response = http.put(f"/budgets/{bid}", headers=bob, json={"category": "Еда", "amount": 1})
    assert response.status_code == 404
    assert response.json()["detail"] == "Бюджет не найден"
    budget = http.get("/budgets", headers=alice).json()[0]
    assert (budget["id"], budget["amount"]) == (bid, 1000)


def test_another_users_budget_cannot_be_deleted(http):
    alice = sign_up(http, "alice@example.test")
    bob = sign_up(http, "bob@example.test")
    http.post("/budgets", headers=alice, json={"category": "Еда", "amount": 1000})
    bid = budget_id(http, alice, "Еда")

    response = http.delete(f"/budgets/{bid}", headers=bob)
    assert response.status_code == 404
    assert response.json()["detail"] == "Бюджет не найден"
    assert [budget["id"] for budget in http.get("/budgets", headers=alice).json()] == [bid]


def test_owner_can_change_and_delete_own_budget(http):
    alice = sign_up(http, "alice@example.test")
    http.post("/budgets", headers=alice, json={"category": "Еда", "amount": 1000})
    bid = budget_id(http, alice, "Еда")

    response = http.put(f"/budgets/{bid}", headers=alice, json={"category": "Еда", "amount": 1500})
    assert response.status_code == 200
    assert response.json() == {"message": "Бюджет обновлен"}
    assert http.get("/budgets", headers=alice).json()[0]["amount"] == 1500

    response = http.delete(f"/budgets/{bid}", headers=alice)
    assert response.status_code == 200
    assert response.json() == {"message": "Бюджет удален"}
    assert http.get("/budgets", headers=alice).json() == []


# --- Exports ------------------------------------------------------------------------------

def test_csv_export_contains_only_the_owners_rows(http):
    alice, bob = seed_export_data(http)

    response = http.get("/export/csv?period=all", headers=bob)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))[1:]
    assert {row[4] for row in rows} == {"Кафе"}
    assert {float(row[3]) for row in rows} == {33.0}
    assert "Аренда" not in response.text and "Зарплата" not in response.text

    alice_rows = list(csv.reader(io.StringIO(http.get("/export/csv?period=all", headers=alice).text)))[1:]
    assert {row[4] for row in alice_rows} == {"Аренда", "Зарплата"}


def test_excel_export_contains_only_the_owners_rows(http):
    openpyxl = pytest.importorskip("openpyxl")
    alice, bob = seed_export_data(http)

    response = http.get("/export/excel?period=all", headers=bob)
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    sheet = openpyxl.load_workbook(io.BytesIO(response.content))["Операции"]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
    assert {row[4] for row in rows} == {"Кафе"}
    assert {row[3] for row in rows} == {33.0}


def test_pdf_export_answers_for_the_owner(http):
    pytest.importorskip("reportlab")
    if api.pdf_fonts() is None:
        pytest.skip("no PDF font with Cyrillic letters is available (PDF_FONT_PATH is not set)")
    alice, bob = seed_export_data(http)

    response = http.get("/export/pdf?period=all", headers=bob)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_pdf_export_draws_only_the_owners_rows(http, drawn_pdf):
    # Checks the text the export hands to reportlab, not the bytes of the file: the embedded
    # font stores glyph codes, so the file itself cannot be searched for words.
    pytest.importorskip("reportlab")
    if api.pdf_fonts() is None:
        pytest.skip("no PDF font with Cyrillic letters is available (PDF_FONT_PATH is not set)")
    alice, bob = seed_export_data(http)

    response = http.get("/export/pdf?period=all", headers=bob)
    assert response.status_code == 200
    assert any("bob@example.test" in text for text in drawn_pdf), "the export's own header was not drawn"
    assert any("Кафе" in text for text in drawn_pdf), "the owner's own row was not drawn"
    for foreign in ("Аренда", "Зарплата", "111", "222"):
        assert not any(foreign in text for text in drawn_pdf), "another user's data reached the PDF: %s" % foreign
