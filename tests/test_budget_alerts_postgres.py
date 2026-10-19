"""Budget alerts against the local PostgreSQL sandbox: the migration of an older
database, level transitions, calendar months, isolation between users and
concurrent requests.

Skipped unless FT_TEST_POSTGRES_URL is set. The sandbox is one local, disposable
database with a dedicated role and the schema ft_test, marked by tests/pg_sandbox.py
(see tests/db_safety.py and the README). The fixtures drop the application tables in
that schema and nowhere else; every statement is qualified with it.
"""
import threading
from decimal import Decimal

import pytest

import database
from db_safety import sandbox_configured, sandbox_sql as sql

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]
psycopg2 = pytest.importorskip("psycopg2")

# A database created before budget alerts existed: no alert columns, no state table.
LEGACY_SCHEMA = """
    CREATE TABLE users (id BIGSERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                        token_version INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE operations (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                             date TEXT NOT NULL, operation_type TEXT NOT NULL CHECK (operation_type IN ('Доход', 'Расход')),
                             amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0), category TEXT);
    CREATE TABLE budgets (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                          category TEXT NOT NULL, amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
                          period TEXT DEFAULT 'month', created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE user_settings (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                                language TEXT DEFAULT 'RU', theme TEXT DEFAULT 'light', currency TEXT DEFAULT 'KZT');
"""
OCT = "16.10.2026, 10:00:00"
NOV = "03.11.2026, 10:00:00"


@pytest.fixture
def legacy(pg_database_url):
    database.close_pool()
    sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
        "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    sql(LEGACY_SCHEMA)
    sql("INSERT INTO users (email, password_hash) VALUES ('a@example.test', 'x'), ('b@example.test', 'x')")
    sql("INSERT INTO user_settings (user_id, language, theme, currency) VALUES (1, 'KZ', 'dark', 'USD'), (2, 'RU', 'light', 'KZT')")
    sql("INSERT INTO budgets (user_id, category, amount) VALUES (1, 'Еда', 100.00)")
    sql("INSERT INTO operations (user_id, date, operation_type, amount, category) VALUES (1, %s, 'Расход', 10, 'Еда')", (OCT,))
    yield
    database.close_pool()


@pytest.fixture
def pg(legacy):
    """The migrated database, emptied of the legacy budget and operation so each test starts clean."""
    database.create_database()
    sql("DELETE FROM ft_test.budgets")
    sql("DELETE FROM ft_test.operations")
    yield


def budget(user_id, category="Еда", amount=100):
    database.add_budget(user_id, category, amount)
    return sql("SELECT max(id) FROM budgets WHERE user_id = %s", (user_id,))[0][0]


def spend(user_id, amount, date=OCT, category="Еда"):
    database.add_operation(user_id, date, "Расход", amount, category)
    return sql("SELECT max(id) FROM operations WHERE user_id = %s", (user_id,))[0][0]


def check(user_id=1, category="Еда", month="2026-10", notify=True):
    return database.evaluate_budget_alerts(user_id, [(category, month, notify)])


def levels(user_id=1):
    return [(row[0], row[1]) for row in sql(
        "SELECT s.month, s.level FROM budget_alert_state s JOIN budgets b ON b.id = s.budget_id "
        "WHERE b.user_id = %s ORDER BY s.month, s.budget_id", (user_id,))]


# --- Migration of an older database --------------------------------------------------

def test_migration_keeps_every_existing_row_and_turns_alerts_on(legacy):
    before = {table: sql(f"SELECT count(*) FROM {table}")[0][0] for table in ("users", "operations", "budgets", "user_settings")}
    database.create_database()
    database.create_database()  # repeatable
    after = {table: sql(f"SELECT count(*) FROM {table}")[0][0] for table in before}
    assert after == before == {"users": 2, "operations": 1, "budgets": 1, "user_settings": 2}
    assert sql("SELECT budget_alerts_inapp, budget_alerts_push FROM user_settings ORDER BY user_id") == [(True, True), (True, True)]
    assert sql("SELECT language, theme, currency FROM user_settings WHERE user_id = 1") == [("KZ", "dark", "USD")]
    assert sql("SELECT amount FROM budgets") == [(Decimal("100.00"),)]
    assert database.get_user_settings(1) == {"language": "KZ", "theme": "dark", "currency": "USD",
                                             "budget_alerts_inapp": True, "budget_alerts_push": True}


def test_new_users_get_both_alerts_on(pg):
    user_id = database.create_user("new@example.test", "hash")
    settings = database.get_user_settings(user_id)
    assert settings["budget_alerts_inapp"] is True and settings["budget_alerts_push"] is True


def test_switches_are_independent_and_false_is_stored(pg):
    database.update_user_settings(1, budget_alerts_inapp=False)
    assert database.get_user_settings(1)["budget_alerts_inapp"] is False
    assert database.get_user_settings(1)["budget_alerts_push"] is True
    database.update_user_settings(1, budget_alerts_push=False)
    database.update_user_settings(1, currency="EUR")  # an unrelated change leaves them alone
    settings = database.get_user_settings(1)
    assert (settings["budget_alerts_inapp"], settings["budget_alerts_push"], settings["currency"]) == (False, False, "EUR")
    database.update_user_settings(1, budget_alerts_inapp=True)
    assert database.get_user_settings(1)["budget_alerts_inapp"] is True
    assert database.get_user_settings(2)["budget_alerts_inapp"] is True, "another user's switches are untouched"


def test_state_check_rejects_levels_outside_0_to_3(pg):
    budget_id = budget(1)
    with pytest.raises(psycopg2.Error):
        sql("INSERT INTO budget_alert_state (budget_id, month, level) VALUES (%s, '2026-10', 4)", (budget_id,))


# --- Level transitions ---------------------------------------------------------------

def test_each_threshold_alerts_once_and_a_repeat_does_not(pg):
    budget(1)
    spend(1, 50)
    assert check() == []
    spend(1, 30)                                   # 80 %
    first = check()
    assert [(a["level"], a["percent"]) for a in first] == [("warning", 80.0)]
    assert first[0]["category"] == "Еда" and first[0]["month"] == "2026-10"
    assert check() == [], "nothing changed: no second alert, as when the page is refreshed"
    spend(1, 5)                                    # 85 %: still the warning level
    assert check() == []
    spend(1, 15)                                   # exactly 100 %
    assert [a["level"] for a in check()] == ["limit"]
    assert check() == []
    spend(1, Decimal("0.01"))                      # a cent over
    assert [a["level"] for a in check()] == ["exceeded"]
    assert check() == []
    spend(1, 500)
    assert check() == [], "more spending at the same level is not a new crossing"
    assert levels() == [("2026-10", 3)]


def test_a_jump_over_levels_gives_one_alert_for_the_highest(pg):
    budget(1)
    spend(1, 250)
    assert [a["level"] for a in check()] == ["exceeded"]


def test_lower_spending_lowers_the_level_silently_and_a_new_crossing_alerts_again(pg):
    budget(1)
    big = spend(1, 120)
    assert [a["level"] for a in check()] == ["exceeded"]
    snapshot = database.delete_operation(big, 1)
    assert snapshot == ("Расход", "Еда", OCT)
    assert check() == [] and levels() == [("2026-10", 0)], "dropped to 0 without an alert"
    spend(1, 85)
    assert [a["level"] for a in check()] == ["warning"]
    spend(1, 20)                                   # 105 %
    assert [a["level"] for a in check()] == ["exceeded"], "crossing again alerts again"
    sql("DELETE FROM ft_test.operations WHERE amount = 20")
    assert check() == [] and levels() == [("2026-10", 1)]


def test_an_unnotified_target_still_moves_the_stored_level(pg):
    budget(1)
    spend(1, 90)
    assert check(notify=False) == [] and levels() == [("2026-10", 1)]
    assert check() == [], "the level was already recorded, so there is nothing new to report"


def test_months_have_their_own_state(pg):
    budget(1)
    spend(1, 90, OCT)
    spend(1, 90, NOV)
    spend(1, 20, "30.09.2026, 23:59:59")
    database.evaluate_budget_alerts(1, [("Еда", "2026-10", True), ("Еда", "2026-11", True)])
    assert levels() == [("2026-10", 1), ("2026-11", 1)]
    assert check(month="2026-09") == []                 # 20 of 100: below the warning level
    assert check(month="2026-10") == [] and check(month="2026-11") == []
    spend(1, 15, NOV)
    assert [a["month"] for a in check(month="2026-11")] == ["2026-11"]
    assert levels() == [("2026-09", 0), ("2026-10", 1), ("2026-11", 3)]


def test_month_boundaries_are_calendar_not_thirty_days(pg):
    budget(1)
    spend(1, 50, "31.10.2026, 23:59:59")
    spend(1, 50, "01.11.2026, 00:00:00")
    spend(1, 99, "01.10.2026, 00:00:00")
    assert [a["level"] for a in check(month="2026-10")] == ["exceeded"]     # 50 + 99 + nothing from November
    assert check(month="2026-11") == [], "50 in November is below 80 %"


def test_dates_nobody_can_read_are_never_counted(pg):
    budget(1)
    spend(1, 500, "someday")
    spend(1, 50)
    assert check() == [] and levels() == [("2026-10", 0)]


def test_only_expenses_in_the_budgets_category_count(pg):
    budget(1)
    database.add_operation(1, OCT, "Доход", 500, "Еда")
    spend(1, 500, category="Дом")
    assert check() == []


def test_a_category_without_a_budget_does_nothing(pg):
    assert database.evaluate_budget_alerts(1, [("Дом", "2026-10", True), ("", "2026-10", True), ("Еда", None, True)]) == []
    assert levels() == []


def test_several_budgets_of_one_category_are_measured_separately(pg):
    budget(1, amount=100)
    budget(1, amount=1000)
    spend(1, 90)
    alerts = check()
    assert [(a["amount"], a["level"]) for a in alerts] == [(100.0, "warning")]
    assert len(levels()) == 2


# --- Editing a budget ----------------------------------------------------------------

def test_changing_the_limit_is_followed_silently_and_later_spending_alerts(pg):
    budget_id = budget(1)
    spend(1, 90)
    assert check(notify=False) == []
    database.update_budget(budget_id, 1, 200)
    assert check(notify=False) == [] and levels() == [("2026-10", 0)]
    spend(1, 80)                                   # 170 of 200
    assert [a["level"] for a in check()] == ["warning"]


def test_deleting_a_budget_removes_its_state(pg):
    budget_id = budget(1)
    spend(1, 90)
    check()
    database.delete_budget(budget_id, 1)
    assert sql("SELECT count(*) FROM budget_alert_state") == [(0,)]


# --- Editing and deleting operations -------------------------------------------------

def test_update_returns_the_previous_values_and_both_sides_are_recalculated(pg):
    budget(1, "Еда", 100)
    budget(1, "Дом", 100)
    operation_id = spend(1, 90, OCT, "Еда")
    assert [a["category"] for a in check(category="Еда")] == ["Еда"]
    previous = database.update_operation(operation_id, 1, NOV, "Расход", 90, "Дом")
    assert previous == ("Расход", "Еда", OCT)
    alerts = database.evaluate_budget_alerts(1, [("Еда", "2026-10", True), ("Дом", "2026-11", True)])
    assert [(a["category"], a["month"], a["level"]) for a in alerts] == [("Дом", "2026-11", "warning")]
    assert dict(levels())["2026-10"] == 0, "the old category and month dropped without an alert"


def test_update_of_an_unknown_or_foreign_operation_changes_nothing(pg):
    operation_id = spend(1, 10)
    with pytest.raises(database.OperationNotFound):
        database.update_operation(operation_id, 2, OCT, "Расход", 999, "Еда")
    with pytest.raises(database.OperationNotFound):
        database.update_operation(424242, 1, OCT, "Расход", 1, "Еда")
    with pytest.raises(database.OperationNotFound):
        database.delete_operation(operation_id, 2)
    assert sql("SELECT amount FROM operations WHERE id = %s", (operation_id,)) == [(Decimal("10.00"),)]


# --- Isolation -----------------------------------------------------------------------

def test_users_never_see_or_trigger_each_others_alerts(pg):
    budget(1)
    budget(2)
    spend(1, 95)
    assert database.evaluate_budget_alerts(2, [("Еда", "2026-10", True)]) == [], "B's budget does not count A's spending"
    assert levels(2) == [("2026-10", 0)]
    assert [a["level"] for a in check(1)] == ["warning"]
    spend(2, 100)
    assert [a["level"] for a in database.evaluate_budget_alerts(2, [("Еда", "2026-10", True)])] == ["limit"]
    assert levels(1) == [("2026-10", 1)], "A's state was not moved by B"


# --- CSV import ----------------------------------------------------------------------

def test_a_repeated_import_inserts_nothing_and_so_alerts_nothing(pg):
    budget(1)
    rows = [(OCT, "Расход", Decimal("90"), "Еда", "hash-1")]
    inserted = database.import_operations(1, rows)
    assert inserted == {"hash-1"}
    assert [a["level"] for a in check()] == ["warning"]
    assert database.import_operations(1, rows) == set(), "the unique import hash still stops duplicates"
    assert sql("SELECT count(*) FROM operations") == [(1,)]
    assert check() == []


# --- Concurrency ---------------------------------------------------------------------

def test_concurrent_requests_report_a_crossing_exactly_once(pg):
    budget(1)
    spend(1, 10)
    assert check() == [] and levels() == [("2026-10", 0)]   # the state row exists, so only its lock serialises
    for _ in range(3):
        spend(1, 30)                                # 100 spent: crossed, nobody has evaluated yet
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            results.append(check())
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sum(len(alerts) for alerts in results) == 1
    assert levels() == [("2026-10", 2)]


def test_concurrent_requests_for_several_budgets_do_not_deadlock(pg):
    for category in ("Еда", "Дом", "Здоровье"):
        budget(1, category, 100)
        spend(1, 90, OCT, category)
    categories = ["Еда", "Дом", "Здоровье"]
    results, errors = [], []
    barrier = threading.Barrier(6)

    def worker(order):
        try:
            barrier.wait()
            results.append(database.evaluate_budget_alerts(1, [(c, "2026-10", True) for c in order]))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    orders = [categories, categories[::-1], categories[1:] + categories[:1]] * 2
    threads = [threading.Thread(target=worker, args=(order,)) for order in orders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors and len(results) == 6
    assert sum(len(alerts) for alerts in results) == 3, "one alert per budget, however the requests interleave"


# --- The whole thing through the HTTP API, on the real database ------------------------

import budget_alerts  # noqa: E402

pytest.importorskip("httpx")
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def http(pg, monkeypatch):
    """The app over the migrated database; October 2026 is the current month."""
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setattr(budget_alerts, "current_month_keys", lambda now=None: ["2026-10"])
    monkeypatch.setattr(budget_alerts, "current_month_key", lambda now=None: "2026-10")
    sql("DELETE FROM ft_test.users")
    api.limiter.reset()
    yield TestClient(api.app)
    api.limiter.reset()


def sign_up(client, email):
    api.limiter.reset()
    token = client.post("/auth/register", json={"email": email, "password": "secret12"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def add_expense(client, headers, amount, category="Еда", date=OCT):
    return client.post("/operations", headers=headers,
                       json={"date": date, "operation_type": "Расход", "amount": amount, "category": category})


def levels_of(email):
    """(month, level) rows of the budgets of the user with this email."""
    return [(row[0], row[1]) for row in sql(
        "SELECT s.month, s.level FROM budget_alert_state s JOIN budgets b ON b.id = s.budget_id "
        "JOIN users u ON u.id = b.user_id WHERE u.email = %s ORDER BY s.month, s.budget_id", (email,))]


def alert_levels(response):
    return [(a["category"], a["level"]) for a in response.json()["alerts"]]


def test_a_budget_journey_alerts_once_per_crossing_and_survives_refreshes(http):
    user = sign_up(http, "journey@example.test")
    assert http.post("/budgets", headers=user, json={"category": "Еда", "amount": 1000}).status_code == 201
    assert alert_levels(add_expense(http, user, 700)) == []
    reached = add_expense(http, user, 100)                                # 800 of 1000
    assert alert_levels(reached) == [("Еда", "warning")]
    assert reached.json()["notification"] == "⚠️ Бюджет на Еда: израсходовано 80%"
    for _ in range(3):                                                    # refreshing the page repeats nothing
        assert http.get("/budgets", headers=user).json()[0]["level"] == 1
        assert http.get("/operations", headers=user).status_code == 200
    assert alert_levels(add_expense(http, user, 50)) == []                # 850: same level
    assert alert_levels(add_expense(http, user, 150)) == [("Еда", "limit")]            # exactly 1000
    over = add_expense(http, user, 1)
    assert alert_levels(over) == [("Еда", "exceeded")] and over.json()["notification"] == "⚠️ Бюджет на Еда превышен!"
    assert alert_levels(add_expense(http, user, 5)) == [], "more spending beyond the limit is not a new crossing"
    budget = http.get("/budgets", headers=user).json()[0]
    assert (budget["spent"], budget["level"], budget["exceeded"], budget["month"]) == (1006.0, 3, True, "2026-10")

    operations = http.get("/operations", headers=user).json()
    for operation in operations:
        if operation["amount"] in (5.0, 1.0, 150.0):
            assert http.delete(f"/operations/{operation['id']}", headers=user).json() == {"message": "Операция удалена"}
    assert http.get("/budgets", headers=user).json()[0]["level"] == 1, "back to 850: the level followed the spending silently"
    assert alert_levels(add_expense(http, user, 160)) == [("Еда", "exceeded")], "crossing again alerts again"


def test_editing_an_operation_moves_spending_between_categories_and_months(http):
    user = sign_up(http, "edit@example.test")
    for category in ("Еда", "Дом"):
        http.post("/budgets", headers=user, json={"category": category, "amount": 1000})
    add_expense(http, user, 850, "Еда")
    operation_id = http.get("/operations", headers=user).json()[0]["id"]
    assert http.get("/budgets", headers=user).json()[0]["level"] == 1

    body = {"date": OCT, "operation_type": "Расход", "amount": 850, "category": "Дом"}
    moved = http.put(f"/operations/{operation_id}", headers=user, json=body)
    assert moved.status_code == 200 and alert_levels(moved) == [("Дом", "warning")]
    levels_now = {b["category"]: b["level"] for b in http.get("/budgets", headers=user).json()}
    assert levels_now == {"Еда": 0, "Дом": 1}, "the old category dropped without an alert"

    to_november = http.put(f"/operations/{operation_id}", headers=user, json={**body, "date": NOV})
    assert to_november.json()["alerts"] == [], "November is not the current month: no alert"
    assert {b["category"]: b["level"] for b in http.get("/budgets", headers=user).json()} == {"Еда": 0, "Дом": 0}
    assert dict(levels_of("edit@example.test"))["2026-11"] == 1, "but its level was recorded"

    as_income = http.put(f"/operations/{operation_id}", headers=user,
                         json={"date": OCT, "operation_type": "Доход", "amount": 850, "category": "Зарплата"})
    assert as_income.status_code == 200 and as_income.json()["alerts"] == []


def test_users_are_isolated_through_the_api(http):
    alice, bob = sign_up(http, "alice@example.test"), sign_up(http, "bob@example.test")
    http.post("/budgets", headers=alice, json={"category": "Еда", "amount": 1000})
    http.post("/budgets", headers=bob, json={"category": "Еда", "amount": 1000})
    alice_budget = http.get("/budgets", headers=alice).json()[0]["id"]
    add_expense(http, alice, 900)                                             # 90 % of Alice's budget
    response = add_expense(http, bob, 10)
    assert response.json()["alerts"] == [], "Alice's spending does not reach Bob's budget"
    bob_budget = http.get("/budgets", headers=bob).json()[0]
    assert (bob_budget["spent"], bob_budget["level"]) == (10.0, 0)
    alice_expense = add_expense(http, alice, 1)
    assert all(a["budget_id"] == alice_budget for a in alice_expense.json()["alerts"])
    assert http.get("/budgets", headers=alice).json()[0]["level"] == 1
    assert http.get("/budgets", headers=bob).json()[0]["level"] == 0
    bob_ops = http.get("/operations", headers=bob).json()
    alice_op = http.get("/operations", headers=alice).json()[0]["id"]
    assert [o["amount"] for o in bob_ops] == [10.0]
    assert http.put(f"/operations/{alice_op}", headers=bob, json=EXPENSE_BODY).status_code == 404
    assert http.delete(f"/operations/{alice_op}", headers=bob).status_code == 404
    users = {row[0]: row[1] for row in sql("SELECT u.email, s.level FROM budget_alert_state s JOIN budgets b ON b.id = s.budget_id JOIN users u ON u.id = b.user_id")}
    assert users == {"alice@example.test": 1, "bob@example.test": 0}


EXPENSE_BODY = {"date": OCT, "operation_type": "Расход", "amount": 5, "category": "Еда"}


def test_the_switch_stops_alerts_but_not_the_bookkeeping(http):
    user = sign_up(http, "switch@example.test")
    http.post("/budgets", headers=user, json={"category": "Еда", "amount": 1000})
    assert http.put("/auth/profile", headers=user, json={"budget_alerts_inapp": False}).status_code == 200
    silent = add_expense(http, user, 850)
    assert silent.json() == {"message": "Операция добавлена", "notification": None, "alerts": []}
    assert http.put("/auth/profile", headers=user, json={"budget_alerts_inapp": True}).status_code == 200
    assert alert_levels(add_expense(http, user, 10)) == [], "the 80 % crossing was recorded while the switch was off"
    assert alert_levels(add_expense(http, user, 200)) == [("Еда", "exceeded")]
    assert http.get("/auth/me", headers=user).json()["settings"]["budget_alerts_inapp"] is True


def test_a_budget_created_after_the_spending_does_not_alert_for_it(http):
    user = sign_up(http, "late@example.test")
    add_expense(http, user, 900)
    http.post("/budgets", headers=user, json={"category": "Еда", "amount": 1000})
    assert levels_of("late@example.test") == [("2026-10", 1)], "the level follows the spending, silently"
    assert alert_levels(add_expense(http, user, 5)) == [], "the 90 % was there before the limit was set"
    assert alert_levels(add_expense(http, user, 100)) == [("Еда", "exceeded")]
    budget_id = http.get("/budgets", headers=user).json()[0]["id"]
    assert http.put(f"/budgets/{budget_id}", headers=user, json={"category": "Дом", "amount": 5000}).status_code == 200
    assert http.get("/budgets", headers=user).json()[0]["category"] == "Еда", "only the amount of a budget changes"
    assert levels_of("late@example.test") == [("2026-10", 0)], "a bigger limit lowered the level silently"


def test_csv_import_alerts_once_and_a_repeat_is_silent(http):
    user = sign_up(http, "import@example.test")
    http.post("/budgets", headers=user, json={"category": "Другое", "amount": 1000})
    body = "Дата;Сумма\n15.10.2026;-300\n16.10.2026;-300\n17.10.2026;-250\n18.10.2026;5000\n".encode()
    url = "/import/csv/commit?date_column=0&amount_column=1"
    first = http.post(url, content=body, headers={**user, "Content-Type": "text/csv"})
    assert first.status_code == 200 and first.json()["imported"] == 4
    assert alert_levels(first) == [("Другое", "warning")], "three expenses, one budget, one alert; the income is ignored"
    assert http.get("/budgets", headers=user).json()[0]["level"] == 1
    again = http.post(url, content=body, headers={**user, "Content-Type": "text/csv"})
    assert again.json()["imported"] == 0 and again.json()["duplicate_rows"] == 4
    assert again.json()["alerts"] == [] and again.json()["notification"] is None
    assert sql("SELECT count(*) FROM operations") == [(4,)], "duplicate protection is unchanged"


def test_imported_rows_of_another_month_do_not_alert(http):
    user = sign_up(http, "oldimport@example.test")
    http.post("/budgets", headers=user, json={"category": "Другое", "amount": 100})
    response = http.post("/import/csv/commit?date_column=0&amount_column=1", headers={**user, "Content-Type": "text/csv"},
                         content="Дата;Сумма\n15.09.2026;-500\n".encode())
    assert response.json()["imported"] == 1 and response.json()["alerts"] == []
    assert dict(levels_of("oldimport@example.test"))["2026-09"] == 3, "recorded, not announced"


def test_the_listing_uses_the_calendar_month_on_real_data(http):
    user = sign_up(http, "calendar@example.test")
    http.post("/budgets", headers=user, json={"category": "Еда", "amount": 100})
    for date, amount in (("30.09.2026, 23:59:59", 500), ("01.10.2026, 00:00:00", 60), ("31.10.2026, 23:59:59", 30.5),
                         ("01.11.2026, 00:00:00", 500), ("someday", 500)):
        database.add_operation(sql("SELECT id FROM users WHERE email = 'calendar@example.test'")[0][0], date, "Расход", amount, "Еда")
    budget = http.get("/budgets", headers=user).json()[0]
    assert (budget["spent"], budget["percent"], budget["level"]) == (90.5, 90.5, 1)


def test_concurrent_requests_through_the_api_alert_once(http):
    user = sign_up(http, "race@example.test")
    http.post("/budgets", headers=user, json={"category": "Еда", "amount": 1000})
    responses, errors = [], []
    barrier = threading.Barrier(6)

    def post():
        try:
            client = TestClient(api.app)
            barrier.wait()
            responses.append(add_expense(client, user, 150))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=post) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors and len(responses) == 6 and all(r.status_code == 201 for r in responses)
    alerts = [a for r in responses for a in r.json()["alerts"]]
    assert [(a["category"], a["level"]) for a in alerts] == [("Еда", "warning")], "900 of 1000 crossed 80 % exactly once"
