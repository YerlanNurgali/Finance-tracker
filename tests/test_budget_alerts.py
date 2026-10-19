"""Budget alert rules: levels, calendar months and the legacy text. No database."""
from datetime import datetime
from decimal import Decimal

import pytest

import budget_alerts as rules

D = Decimal


@pytest.mark.parametrize("spent,expected", [
    ("0", 0), ("79.99", 0), ("79.999", 0), ("80", 1), ("80.00", 1), ("99.99", 1),
    ("100", 2), ("100.00", 2), ("100.01", 3), ("250", 3),
])
def test_levels_against_a_budget_of_100(spent, expected):
    assert rules.level_for(D(spent), D("100")) == expected


@pytest.mark.parametrize("amount,spent,expected", [
    ("0.01", "0.00", 0), ("0.01", "0.01", 2), ("1000", "800", 1), ("1000", "799.99", 0),
    ("999999999999.99", "799999999999.99", 0), ("999999999999.99", "799999999999.992", 1),
    ("33.33", "26.67", 1), ("33.33", "26.66", 0),
])
def test_levels_use_exact_decimal_arithmetic(amount, spent, expected):
    assert rules.level_for(D(spent), D(amount)) == expected


def test_a_budget_without_a_positive_amount_never_alerts():
    assert rules.level_for(D("5"), D("0")) == 0
    assert rules.level_for(5, -1) == 0


def test_levels_accept_floats_and_ints_from_older_code_paths():
    assert rules.level_for(80.0, 100) == 1
    assert rules.level_for(100, 100.0) == 2


@pytest.mark.parametrize("date,month", [
    ("01.10.2026, 00:00:00", "2026-10"),          # the format the frontend and the import store
    ("31.10.2026, 23:59:59", "2026-10"),
    ("01.11.2026, 00:00:00", "2026-11"),
    ("31.12.2026, 23:59:59", "2026-12"),
    ("01.01.2027, 00:00:00", "2027-01"),
    ("29.02.2028, 12:00:00", "2028-02"),          # leap year
    ("01.03.2028, 00:00:00", "2028-03"),
    ("28.02.2026, 23:59:59", "2026-02"),
    ("01.03.2026, 00:00:00", "2026-03"),
    ("16.10.2026 10:00", "2026-10"),
    ("16.10.2026, 10:00", "2026-10"),
    ("2026-10-16T10:00", "2026-10"),
    ("2026-10-16", "2026-10"),
])
def test_month_key_follows_the_calendar(date, month):
    assert rules.month_key(date) == month


@pytest.mark.parametrize("date", ["", "yesterday", "29.02.2026, 10:00:00", "32.10.2026, 10:00:00", "2026-13-01", None, 20261016])
def test_an_unreadable_date_has_no_month(date):
    assert rules.month_key(date) is None


def test_month_spent_counts_only_the_calendar_month():
    rows = [
        ("30.09.2026, 23:59:59", D("1")),   # last day before: not counted
        ("01.10.2026, 00:00:00", D("2")),
        ("31.10.2026, 23:59:59", D("4")),
        ("01.11.2026, 00:00:00", D("8")),   # first day after: not counted
        ("garbage", D("16")),               # unreadable dates were never counted either
        ("2026-10-15", 32),
    ]
    assert rules.month_spent(rows, "2026-10") == D("38")
    assert rules.month_spent(rows, "2026-11") == D("8")
    assert rules.month_spent([], "2026-10") == D("0")


def test_thirty_days_and_a_calendar_month_differ():
    # 31 days before 1 Nov is still October 1st; a rolling 30-day window would drop it.
    assert rules.month_spent([("01.10.2026, 00:00:00", D("5"))], "2026-10") == D("5")


@pytest.mark.parametrize("now,keys", [
    (datetime(2026, 10, 16, 12, 0), ["2026-10"]),
    (datetime(2026, 10, 31, 20, 0), ["2026-10", "2026-11"]),
    (datetime(2026, 11, 1, 3, 0), ["2026-10", "2026-11"]),
    (datetime(2026, 12, 31, 23, 0), ["2026-12", "2027-01"]),
    (datetime(2027, 1, 1, 0, 30), ["2026-12", "2027-01"]),
    (datetime(2028, 2, 28, 12, 0), ["2028-02"]),
    (datetime(2028, 2, 29, 12, 0), ["2028-02", "2028-03"]),  # leap day is the last day
    (datetime(2026, 2, 28, 12, 0), ["2026-02", "2026-03"]),   # not a leap year
])
def test_current_months_tolerate_the_clock_difference_at_month_ends(now, keys):
    assert rules.current_month_keys(now) == keys


def test_alert_payload_and_most_severe():
    warning = rules.make_alert(1, "Еда", "2026-10", rules.WARNING, D("85"), D("100"))
    limit = rules.make_alert(2, "Дом", "2026-10", rules.LIMIT, D("50"), D("50"))
    exceeded = rules.make_alert(3, "Еда", "2026-10", rules.EXCEEDED, D("120.5"), D("100"))
    assert warning == {"budget_id": 1, "category": "Еда", "month": "2026-10", "level": "warning",
                       "percent": 85.0, "spent": 85.0, "amount": 100.0}
    assert exceeded["percent"] == 120.5
    assert rules.most_severe([warning, exceeded, limit]) is exceeded
    assert rules.most_severe([]) is None


def test_the_exceeded_text_is_the_one_the_notification_field_always_had():
    assert rules.legacy_text({"category": "Еда", "level": "exceeded", "percent": 120.0}) == "⚠️ Бюджет на Еда превышен!"
    assert rules.legacy_text({"category": "Еда", "level": "limit", "percent": 100.0}) == "⚠️ Бюджет на Еда исчерпан"
    assert rules.legacy_text({"category": "Еда", "level": "warning", "percent": 80.0}) == "⚠️ Бюджет на Еда: израсходовано 80%"
    assert rules.legacy_text({"category": "Еда", "level": "warning", "percent": 83.3}) == "⚠️ Бюджет на Еда: израсходовано 83.3%"


# --- The API around the rules: what is recalculated, when, and what the client gets ----
# The data layer is replaced, so no database is involved; the real SQL is covered in
# tests/test_budget_alerts_postgres.py.

pytest.importorskip("httpx")
import api  # noqa: E402
import database  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

USER_ID = 7
OCT_OP = "16.10.2026, 10:00:00"
NOV_OP = "03.11.2026, 10:00:00"
EXPENSE = {"date": OCT_OP, "operation_type": "Расход", "amount": 10, "category": "Еда"}
ALERT = {"budget_id": 1, "category": "Еда", "month": "2026-10", "level": "warning",
         "percent": 85.0, "spent": 85.0, "amount": 100.0}


@pytest.fixture
def env(monkeypatch):
    """A signed-in client; October 2026 is the current month; evaluations are recorded."""
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user,
                        lambda: {"id": USER_ID, "email": "user@example.test"})
    monkeypatch.setattr(rules, "current_month_keys", lambda now=None: ["2026-10"])
    monkeypatch.setattr(rules, "current_month_key", lambda now=None: "2026-10")
    state = types_namespace(calls=[], alerts=[], settings={"currency": "KZT"}, previous=None, budgets=[])

    def evaluate(user_id, targets):
        state.calls.append((user_id, sorted(targets)))
        return list(state.alerts)

    monkeypatch.setattr(database, "evaluate_budget_alerts", evaluate)
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: state.settings)
    monkeypatch.setattr(database, "add_operation", lambda *args: None)
    monkeypatch.setattr(database, "update_operation", lambda *args: state.previous)
    monkeypatch.setattr(database, "delete_operation", lambda *args: state.previous)
    monkeypatch.setattr(database, "add_budget", lambda *args: None)
    monkeypatch.setattr(database, "update_budget", lambda *args: None)
    monkeypatch.setattr(database, "get_budgets", lambda user_id: state.budgets)
    state.client = TestClient(api.app)
    return state


def types_namespace(**values):
    import types
    return types.SimpleNamespace(**values)


def test_a_new_expense_in_the_current_month_is_measured_and_may_alert(env):
    env.alerts = [ALERT]
    response = env.client.post("/operations", json=EXPENSE)
    assert response.status_code == 201
    assert env.calls == [(USER_ID, [("Еда", "2026-10", True)])]
    body = response.json()
    assert body == {"message": "Операция добавлена", "alerts": [ALERT], "notification": "⚠️ Бюджет на Еда: израсходовано 85%"}


def test_an_expense_of_a_past_month_updates_the_level_without_an_alert(env):
    env.client.post("/operations", json={**EXPENSE, "date": "15.09.2026, 10:00:00"})
    assert env.calls == [(USER_ID, [("Еда", "2026-09", False)])]


def test_the_clock_difference_at_a_month_end_still_alerts(env, monkeypatch):
    monkeypatch.setattr(rules, "current_month_keys", lambda now=None: ["2026-10", "2026-11"])
    env.client.post("/operations", json={**EXPENSE, "date": NOV_OP})
    assert env.calls == [(USER_ID, [("Еда", "2026-11", True)])]


def test_an_income_needs_no_recalculation(env):
    response = env.client.post("/operations", json={**EXPENSE, "operation_type": "Доход", "category": "Зарплата"})
    assert response.status_code == 201
    assert env.calls == []
    assert response.json() == {"message": "Операция добавлена", "notification": None, "alerts": []}


# An unreadable date used to be stored and silently left out of every budget. The API now refuses it
# (api.checked_operation_date), so nothing is saved and there is nothing to recalculate.
@pytest.mark.parametrize("date", ["not a date", "yesterday", "31.02.2026, 10:00:00", "2026-10-16 10:00", "   "])
def test_an_unreadable_date_is_refused_and_nothing_is_saved_or_recalculated(env, monkeypatch, date):
    saved = []
    monkeypatch.setattr(database, "add_operation", lambda *args: saved.append(args))
    response = env.client.post("/operations", json={**EXPENSE, "date": date})
    assert response.status_code == 422
    assert response.json()["detail"] == api.DATE_INVALID
    assert saved == []
    assert env.calls == []


def test_the_switch_hides_alerts_but_the_level_is_still_recorded(env):
    env.alerts = [ALERT]
    env.settings = {"budget_alerts_inapp": False}
    response = env.client.post("/operations", json=EXPENSE)
    assert env.calls == [(USER_ID, [("Еда", "2026-10", True)])], "state advances, so enabling later does not replay old crossings"
    assert response.json() == {"message": "Операция добавлена", "notification": None, "alerts": []}


@pytest.mark.parametrize("settings", [{"currency": "KZT"}, {"budget_alerts_inapp": True}])
def test_alerts_are_on_unless_switched_off(env, settings):
    env.alerts = [ALERT]
    env.settings = settings
    assert env.client.post("/operations", json=EXPENSE).json()["alerts"] == [ALERT]


def test_the_most_severe_alert_is_the_legacy_notification(env):
    exceeded = {**ALERT, "level": "exceeded", "percent": 120.0, "category": "Дом"}
    env.alerts = [ALERT, exceeded]
    body = env.client.post("/operations", json=EXPENSE).json()
    assert body["notification"] == "⚠️ Бюджет на Дом превышен!" and len(body["alerts"]) == 2


def test_editing_measures_the_old_and_the_new_side(env):
    env.previous = ("Расход", "Дом", NOV_OP)
    response = env.client.put("/operations/5", json=EXPENSE)
    assert response.status_code == 200
    assert env.calls == [(USER_ID, [("Дом", "2026-11", False), ("Еда", "2026-10", True)])]
    assert set(response.json()) == {"message", "notification", "alerts"}


def test_editing_within_one_category_and_month_can_alert(env):
    env.previous = ("Расход", "Еда", "01.10.2026, 08:00:00")
    env.client.put("/operations/5", json=EXPENSE)
    assert env.calls == [(USER_ID, [("Еда", "2026-10", True)])], "old and new are the same budget and month"


@pytest.mark.parametrize("previous,body,expected", [
    (("Доход", "Зарплата", OCT_OP), EXPENSE, [("Еда", "2026-10", True)]),
    (("Расход", "Еда", OCT_OP), {**EXPENSE, "operation_type": "Доход", "category": "Зарплата"}, [("Еда", "2026-10", False)]),
    (None, EXPENSE, [("Еда", "2026-10", True)]),
])
def test_editing_between_income_and_expense(env, previous, body, expected):
    env.previous = previous
    env.client.put("/operations/5", json=body)
    assert env.calls == [(USER_ID, expected)]


def test_deleting_recalculates_silently_and_keeps_its_response(env):
    env.alerts = [ALERT]
    env.previous = ("Расход", "Еда", OCT_OP)
    response = env.client.delete("/operations/5")
    assert response.status_code == 200 and response.json() == {"message": "Операция удалена"}
    assert env.calls == [(USER_ID, [("Еда", "2026-10", False)])]


def test_deleting_an_income_or_without_a_snapshot_recalculates_nothing(env):
    env.previous = ("Доход", "Зарплата", OCT_OP)
    assert env.client.delete("/operations/5").status_code == 200
    env.previous = None
    assert env.client.delete("/operations/5").status_code == 200
    assert env.calls == []


def test_a_new_budget_follows_the_spending_without_an_alert(env):
    env.alerts = [ALERT]
    response = env.client.post("/budgets", json={"category": " Еда ", "amount": 100})
    assert response.status_code == 201 and response.json() == {"message": "Бюджет добавлен"}
    assert env.calls == [(USER_ID, [("Еда", "2026-10", False)])]


def test_editing_a_budget_uses_the_stored_category_not_the_one_in_the_request(env):
    env.budgets = [(5, "Дом", Decimal("100"), "month"), (6, "Еда", Decimal("100"), "month")]
    response = env.client.put("/budgets/5", json={"category": "Еда", "amount": 300})
    assert response.status_code == 200 and response.json() == {"message": "Бюджет обновлен"}
    assert env.calls == [(USER_ID, [("Дом", "2026-10", False)])]


def test_a_failing_budget_lookup_after_an_edit_does_not_fail_the_edit(env, monkeypatch):
    monkeypatch.setattr(database, "get_budgets", lambda user_id: (_ for _ in ()).throw(RuntimeError("driver")))
    assert env.client.put("/budgets/5", json={"category": "Еда", "amount": 300}).status_code == 200
    assert env.calls == []


CSV_BODY = "Дата;Сумма\n15.10.2026;-100\n16.10.2026;-50\n17.10.2026;200\n".encode()
CSV_HEADERS = {"Content-Type": "text/csv"}
CSV_URL = "/import/csv/commit?date_column=0&amount_column=1"


def _import(env, monkeypatch, store):
    """Pretends only the rows ``store`` picks were inserted, the way the unique index would."""
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: env.settings)

    def import_operations(user_id, operations):
        return {row[4] for row in operations if store(row)}

    monkeypatch.setattr(database, "import_operations", import_operations)
    return env.client.post(CSV_URL, content=CSV_BODY, headers=CSV_HEADERS)


def test_an_import_measures_only_the_expenses_it_really_stored(env, monkeypatch):
    env.alerts = [ALERT]
    response = _import(env, monkeypatch, lambda row: row[1] == "Расход" and row[0].startswith("15.10"))
    assert response.status_code == 200
    assert env.calls == [(USER_ID, [("Другое", "2026-10", True)])]
    body = response.json()
    assert body["imported"] == 1 and body["duplicate_rows"] == 2
    assert body["alerts"] == [ALERT] and body["notification"].startswith("⚠️ Бюджет на Еда")


def test_a_repeated_import_stores_nothing_and_recalculates_nothing(env, monkeypatch):
    response = _import(env, monkeypatch, lambda row: False)
    assert response.status_code == 200 and response.json()["imported"] == 0
    assert env.calls == []
    assert response.json()["alerts"] == [] and response.json()["notification"] is None


def test_an_import_of_income_only_recalculates_nothing(env, monkeypatch):
    _import(env, monkeypatch, lambda row: row[1] == "Доход")
    assert env.calls == []


def test_many_imported_expenses_of_one_budget_are_measured_once(env, monkeypatch):
    _import(env, monkeypatch, lambda row: row[1] == "Расход")
    assert env.calls == [(USER_ID, [("Другое", "2026-10", True)])], "two rows, one category and month, one measurement"


def test_a_failing_measurement_does_not_fail_the_import(env, monkeypatch):
    monkeypatch.setattr(database, "evaluate_budget_alerts", lambda *a: (_ for _ in ()).throw(RuntimeError("driver")))
    response = _import(env, monkeypatch, lambda row: row[1] == "Расход")
    assert response.status_code == 200 and response.json()["imported"] == 2
    assert response.json()["alerts"] == []


# --- GET /budgets: the calendar month and the level -----------------------------------

def _budgets_view(env, monkeypatch, amount, rows):
    monkeypatch.setattr(database, "get_budgets", lambda user_id: [(1, "Еда", Decimal(amount), "month")])
    monkeypatch.setattr(database, "get_operations", lambda user_id: rows)
    response = env.client.get("/budgets")
    assert response.status_code == 200
    return response.json()[0]


def test_budgets_are_measured_over_the_calendar_month(env, monkeypatch):
    rows = [
        (1, "30.09.2026, 23:59:59", "Расход", Decimal("500"), "Еда"),     # the day before
        (2, "01.10.2026, 00:00:00", "Расход", Decimal("80"), "Еда"),     # first moment: more than 30 days ago on 31 Oct
        (3, "31.10.2026, 23:59:59", "Расход", Decimal("10.50"), "Еда"),
        (4, "01.11.2026, 00:00:00", "Расход", Decimal("500"), "Еда"),     # the day after
        (5, "15.10.2026, 10:00:00", "Доход", Decimal("500"), "Еда"),       # income
        (6, "15.10.2026, 10:00:00", "Расход", Decimal("500"), "Дом"),     # another category
        (7, "someday", "Расход", Decimal("500"), "Еда"),                 # unreadable date
    ]
    budget = _budgets_view(env, monkeypatch, "100", rows)
    assert (budget["spent"], budget["percent"], budget["remaining"]) == (90.5, 90.5, 9.5)
    assert (budget["level"], budget["exceeded"], budget["month"]) == (1, False, "2026-10")
    assert budget["category"] == "Еда" and budget["period"] == "month" and budget["amount"] == 100.0


@pytest.mark.parametrize("spent,level,exceeded", [
    ("79.99", 0, False), ("80.00", 1, False), ("99.99", 1, False), ("100.00", 2, False), ("100.01", 3, True),
])
def test_budget_levels_in_the_listing(env, monkeypatch, spent, level, exceeded):
    budget = _budgets_view(env, monkeypatch, "100", [(1, OCT_OP, "Расход", Decimal(spent), "Еда")])
    assert (budget["level"], budget["exceeded"]) == (level, exceeded)


def test_a_budget_with_no_spending_in_the_month(env, monkeypatch):
    budget = _budgets_view(env, monkeypatch, "100", [(1, NOV_OP, "Расход", Decimal("50"), "Еда")])
    assert (budget["spent"], budget["percent"], budget["remaining"], budget["level"]) == (0.0, 0.0, 100.0, 0)


def test_looking_at_the_budgets_never_creates_an_alert(env, monkeypatch):
    _budgets_view(env, monkeypatch, "100", [(1, OCT_OP, "Расход", Decimal("95"), "Еда")])
    env.client.get("/budgets")
    assert env.calls == [], "a page refresh cannot repeat a notification"


# --- Settings -------------------------------------------------------------------------

def test_the_profile_passes_both_switches_on(env, monkeypatch):
    saved = []
    monkeypatch.setattr(database, "update_user_settings", lambda user_id, **kwargs: saved.append((user_id, kwargs)))
    response = env.client.put("/auth/profile", json={"budget_alerts_inapp": False, "budget_alerts_push": True})
    assert response.status_code == 200
    assert saved == [(USER_ID, {"language": None, "theme": None, "currency": None,
                                "budget_alerts_inapp": False, "budget_alerts_push": True})]


def test_a_profile_change_without_the_switches_leaves_them_alone(env, monkeypatch):
    saved = []
    monkeypatch.setattr(database, "update_user_settings", lambda user_id, **kwargs: saved.append(kwargs))
    env.client.put("/auth/profile", json={"currency": "USD"})
    assert saved[0]["budget_alerts_inapp"] is None and saved[0]["budget_alerts_push"] is None


@pytest.mark.parametrize("value", ["yes", "false", 1, 0, "true", [True], {}])
def test_the_switches_must_be_real_booleans(env, monkeypatch, value):
    monkeypatch.setattr(database, "update_user_settings", lambda *a, **k: pytest.fail("must not be saved"))
    assert env.client.put("/auth/profile", json={"budget_alerts_inapp": value}).status_code == 422
    assert env.client.put("/auth/profile", json={"budget_alerts_push": value}).status_code == 422


def test_the_account_settings_include_the_switches(env):
    env.settings = {"language": "RU", "theme": "light", "currency": "KZT",
                    "budget_alerts_inapp": False, "budget_alerts_push": True}
    assert env.client.get("/auth/me").json()["settings"] == env.settings
