"""User categories against the local PostgreSQL sandbox: the table, its foreign key and unique
index, isolation between users, rename / delete with the move of operations, the budget guard
and concurrent requests.

Skipped unless FT_TEST_POSTGRES_URL is set. The sandbox is one local, disposable database with
a dedicated role and the schema ft_test, marked by tests/pg_sandbox.py (see tests/db_safety.py
and the README). The fixture drops the application tables in that schema and nowhere else;
every statement of the tests themselves goes through the sandbox helper or the application's
own pool, both of which point at the sandbox only.

The data-layer functions of database.py are used directly (the HTTP layer is covered with a
stand-in database in tests/test_categories.py). The unique-index checks use ASCII names: how
the database's lower() treats Cyrillic depends on the locale it was created with, while the
application compares names itself, with Python's casefold().
"""
import threading
from types import SimpleNamespace

import pytest

import database
from db_safety import sandbox_configured, sandbox_sql as sql

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]
psycopg2 = pytest.importorskip("psycopg2")

EXPENSE, INCOME = "Расход", "Доход"
OTHER = "Другое"
DATE = "16.10.2026, 10:00:00"
INDEX = "idx_user_categories_user_kind_name"


@pytest.fixture
def pg(pg_database_url):
    """Fresh application tables built by create_database(), and two users."""
    database.close_pool()
    sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
        "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    database.create_database()
    users = SimpleNamespace(
        a=database.create_user("a@example.test", "hash"),
        b=database.create_user("b@example.test", "hash"),
    )
    yield users
    database.close_pool()


def insert_category(user_id, kind, name):
    sql("INSERT INTO user_categories (user_id, kind, name) VALUES (%s, %s, %s)", (user_id, kind, name))


def stored(user_id):
    return [(row[0], row[1]) for row in sql(
        "SELECT kind, name FROM user_categories WHERE user_id = %s ORDER BY id", (user_id,))]


def operation_categories(user_id, kind):
    return [row[0] for row in sql(
        "SELECT category FROM operations WHERE user_id = %s AND operation_type = %s ORDER BY id", (user_id, kind))]


def budget_categories(user_id):
    return [row[0] for row in sql("SELECT category FROM budgets WHERE user_id = %s ORDER BY id", (user_id,))]


def spend(user_id, category, kind=EXPENSE):
    database.add_operation(user_id, DATE, kind, 10, category)


WORKER_TIMEOUT = 30  # seconds a barrier or a thread may take; the sandbox answers in milliseconds


def run_in_parallel(count, job):
    """Runs job(index) in ``count`` threads released together; returns ("ok", value) or ("error", exception).

    A job's own exception is returned, because the test decides which errors are expected. Nothing waits
    forever: the barrier and every join have WORKER_TIMEOUT, a thread that cannot pass the barrier gets
    BrokenBarrierError (returned as an error), and a thread still running afterwards fails the test."""
    barrier = threading.Barrier(count, timeout=WORKER_TIMEOUT)
    results = [None] * count

    def worker(index):
        try:
            barrier.wait()
        except threading.BrokenBarrierError as exc:
            results[index] = ("error", exc)
            return
        try:
            results[index] = ("ok", job(index))
        except Exception as exc:  # collected: the test decides which errors are expected
            results[index] = ("error", exc)

    threads = [threading.Thread(target=worker, args=(index,), name="worker-%d" % index) for index in range(count)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WORKER_TIMEOUT)
    finally:
        barrier.abort()  # releases a thread still waiting on the barrier; harmless once every thread has passed it
    stuck = [thread.name for thread in threads if thread.is_alive()]
    assert not stuck, "threads still running %d s after the start: %s" % (WORKER_TIMEOUT, ", ".join(stuck))
    # A thread that ended with an error which is not an Exception (BaseException) records no result.
    missing = [thread.name for thread, result in zip(threads, results) if result is None]
    assert not missing, "threads ended without a result (not an Exception): %s" % ", ".join(missing)
    return results


# --- The table, its foreign key and its index ----------------------------------------------

def test_create_database_makes_the_table_and_is_repeatable(pg):
    database.create_database()
    database.create_database()
    columns = sql("SELECT column_name, is_nullable FROM information_schema.columns "
                  "WHERE table_schema = 'ft_test' AND table_name = 'user_categories' ORDER BY ordinal_position")
    assert columns == [("id", "NO"), ("user_id", "NO"), ("kind", "NO"), ("name", "NO")]
    assert sql("SELECT count(*) FROM pg_indexes WHERE schemaname = 'ft_test' AND indexname = %s", (INDEX,)) == [(1,)]


def test_the_foreign_key_points_at_users_and_cascades(pg):
    keys = sql("SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
               "WHERE conrelid = 'ft_test.user_categories'::regclass AND contype = 'f'")
    assert len(keys) == 1 and keys[0][0] in ("users", "ft_test.users") and keys[0][1] == "c"
    with pytest.raises(psycopg2.IntegrityError):
        insert_category(987654321, EXPENSE, "Хобби")  # no such user
    database.add_user_category(pg.a, EXPENSE, "Хобби")
    database.add_user_category(pg.b, EXPENSE, "Хобби")
    database.delete_user_account(pg.a)
    assert stored(pg.a) == [] and stored(pg.b) == [(EXPENSE, "Хобби")], "only the deleted user's categories go"


def test_the_kind_is_limited_to_income_and_expense(pg):
    for kind in ("Перевод", "расход", ""):
        with pytest.raises(psycopg2.IntegrityError):
            insert_category(pg.a, kind, "Хобби")
    assert stored(pg.a) == []
    insert_category(pg.a, INCOME, "Хобби")
    insert_category(pg.a, EXPENSE, "Хобби")
    assert stored(pg.a) == [(INCOME, "Хобби"), (EXPENSE, "Хобби")]


def test_the_unique_index_is_per_user_and_kind_and_ignores_letter_case(pg):
    index = sql("SELECT indexdef FROM pg_indexes WHERE schemaname = 'ft_test' AND indexname = %s", (INDEX,))[0][0]
    assert "UNIQUE" in index and "user_id, kind, lower(name)" in index
    insert_category(pg.a, EXPENSE, "Games")
    for name in ("Games", "GAMES", "games"):
        with pytest.raises(psycopg2.IntegrityError):
            insert_category(pg.a, EXPENSE, name)
    insert_category(pg.a, INCOME, "Games")      # the other kind
    insert_category(pg.b, EXPENSE, "Games")     # another user
    insert_category(pg.a, EXPENSE, "Games 2")   # another name
    assert stored(pg.a) == [(EXPENSE, "Games"), (INCOME, "Games"), (EXPENSE, "Games 2")]
    assert stored(pg.b) == [(EXPENSE, "Games")]


# --- Create, list and isolation ----------------------------------------------------------------

def test_a_category_is_stored_listed_and_refused_twice(pg):
    created = database.add_user_category(pg.a, EXPENSE, "  Хобби ")
    assert created == {"id": created["id"], "kind": EXPENSE, "name": "Хобби", "builtin": False}
    assert stored(pg.a) == [(EXPENSE, "Хобби")]
    listed = database.list_categories(pg.a)
    assert listed[-1] == created and len(listed) == len(database.category_items([])) + 1
    for name in ("Хобби", "хобби", "ХОББИ", " хобби "):
        with pytest.raises(database.CategoryExists):
            database.add_user_category(pg.a, EXPENSE, name)
    database.add_user_category(pg.a, INCOME, "Хобби")
    assert len(stored(pg.a)) == 2


def test_built_in_names_are_refused_in_every_language(pg):
    for name in ("Еда", "food", "Тамақ", "OTHER", "Басқа"):
        with pytest.raises(database.InvalidInput):
            database.add_user_category(pg.a, EXPENSE, name)
    assert stored(pg.a) == []


def test_users_see_and_change_only_their_own_categories(pg):
    mine = database.add_user_category(pg.a, EXPENSE, "Хобби")
    theirs = database.add_user_category(pg.b, EXPENSE, "Хобби")
    spend(pg.a, "Хобби")
    spend(pg.b, "Хобби")
    assert [c["id"] for c in database.list_categories(pg.a) if not c["builtin"]] == [mine["id"]]
    assert [c["id"] for c in database.list_categories(pg.b) if not c["builtin"]] == [theirs["id"]]
    with pytest.raises(database.CategoryNotFound):
        database.rename_user_category(theirs["id"], pg.a, "Чужое")
    with pytest.raises(database.CategoryNotFound):
        database.delete_user_category(theirs["id"], pg.a)
    assert stored(pg.b) == [(EXPENSE, "Хобби")] and operation_categories(pg.b, EXPENSE) == ["Хобби"]


def test_ids_the_column_cannot_hold_are_not_found(pg):
    for category_id in (0, -1, 2 ** 63, 10 ** 30):
        with pytest.raises(database.CategoryNotFound):
            database.rename_user_category(category_id, pg.a, "Новая")
        with pytest.raises(database.CategoryNotFound):
            database.delete_user_category(category_id, pg.a)


# --- Rename ----------------------------------------------------------------------------------------

def test_renaming_updates_the_operations_and_budgets_of_that_user_and_kind_only(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    spend(pg.a, "Хобби")
    spend(pg.a, "Хобби")
    spend(pg.a, "Хобби", INCOME)   # the income category of the same name is another category
    spend(pg.a, "Еда")
    spend(pg.b, "Хобби")
    database.add_budget(pg.a, "Хобби", 100)
    database.add_budget(pg.b, "Хобби", 100)
    result = database.rename_user_category(category["id"], pg.a, " Увлечения ")
    assert result == {"category": {"id": category["id"], "kind": EXPENSE, "name": "Увлечения", "builtin": False},
                      "operations_updated": 2, "budgets_updated": 1}
    assert stored(pg.a) == [(EXPENSE, "Увлечения")]
    assert operation_categories(pg.a, EXPENSE) == ["Увлечения", "Увлечения", "Еда"]
    assert operation_categories(pg.a, INCOME) == ["Хобби"]
    assert operation_categories(pg.b, EXPENSE) == ["Хобби"]
    assert budget_categories(pg.a) == ["Увлечения"] and budget_categories(pg.b) == ["Хобби"]


def test_renaming_to_a_taken_or_built_in_name_changes_nothing(pg):
    first = database.add_user_category(pg.a, EXPENSE, "Хобби")
    database.add_user_category(pg.a, EXPENSE, "Игры")
    spend(pg.a, "Хобби")
    with pytest.raises(database.CategoryExists):
        database.rename_user_category(first["id"], pg.a, "игры")
    with pytest.raises(database.InvalidInput):
        database.rename_user_category(first["id"], pg.a, "Food")
    assert stored(pg.a) == [(EXPENSE, "Хобби"), (EXPENSE, "Игры")]
    assert operation_categories(pg.a, EXPENSE) == ["Хобби"]


def test_a_change_of_letter_case_is_a_rename_and_the_same_name_changes_nothing(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    spend(pg.a, "Хобби")
    same = database.rename_user_category(category["id"], pg.a, "Хобби")
    assert (same["operations_updated"], same["budgets_updated"]) == (0, 0)
    changed = database.rename_user_category(category["id"], pg.a, "ХОББИ")
    assert changed["operations_updated"] == 1
    assert stored(pg.a) == [(EXPENSE, "ХОББИ")] and operation_categories(pg.a, EXPENSE) == ["ХОББИ"]


def test_renaming_an_income_category_leaves_the_budgets_alone(pg):
    category = database.add_user_category(pg.a, INCOME, "Хобби")
    spend(pg.a, "Хобби", INCOME)
    database.add_budget(pg.a, "Хобби", 100)
    result = database.rename_user_category(category["id"], pg.a, "Подработка")
    assert (result["operations_updated"], result["budgets_updated"]) == (1, 0)
    assert budget_categories(pg.a) == ["Хобби"]


# --- Delete ------------------------------------------------------------------------------------------

def test_deleting_moves_the_operations_to_other_and_keeps_the_history(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    database.add_user_category(pg.a, INCOME, "Хобби")
    for _ in range(3):
        spend(pg.a, "Хобби")
    spend(pg.a, "Хобби", INCOME)
    spend(pg.b, "Хобби")
    before = sql("SELECT count(*) FROM operations")[0][0]
    result = database.delete_user_category(category["id"], pg.a)
    assert result == {"kind": EXPENSE, "name": "Хобби", "moved_to": OTHER, "operations_moved": 3}
    assert operation_categories(pg.a, EXPENSE) == [OTHER] * 3
    assert operation_categories(pg.a, INCOME) == ["Хобби"]
    assert operation_categories(pg.b, EXPENSE) == ["Хобби"]
    assert sql("SELECT count(*) FROM operations")[0][0] == before, "no operation is lost"
    assert stored(pg.a) == [(INCOME, "Хобби")]


def test_operations_can_be_moved_to_a_chosen_category(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    database.add_user_category(pg.a, EXPENSE, "Игры")
    spend(pg.a, "Хобби")
    assert database.delete_user_category(category["id"], pg.a, "игры")["moved_to"] == "Игры"
    assert operation_categories(pg.a, EXPENSE) == ["Игры"]


def test_a_bad_target_deletes_and_moves_nothing(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    database.add_user_category(pg.a, INCOME, "Подарки")
    spend(pg.a, "Хобби")
    for target in ("Хобби", "Неизвестная", "Зарплата", "Подарки"):
        with pytest.raises(database.InvalidInput):
            database.delete_user_category(category["id"], pg.a, target)
    assert (EXPENSE, "Хобби") in stored(pg.a) and operation_categories(pg.a, EXPENSE) == ["Хобби"]


def test_an_expense_category_with_a_budget_is_not_deleted(pg):
    category = database.add_user_category(pg.a, EXPENSE, "Хобби")
    spend(pg.a, "Хобби")
    database.add_budget(pg.a, "Хобби", 100)
    with pytest.raises(database.CategoryInUse):
        database.delete_user_category(category["id"], pg.a)
    assert stored(pg.a) == [(EXPENSE, "Хобби")] and operation_categories(pg.a, EXPENSE) == ["Хобби"]
    assert budget_categories(pg.a) == ["Хобби"]
    budget_id = sql("SELECT id FROM budgets WHERE user_id = %s", (pg.a,))[0][0]
    database.delete_budget(budget_id, pg.a)
    assert database.delete_user_category(category["id"], pg.a)["operations_moved"] == 1
    assert stored(pg.a) == []


def test_another_users_budget_or_an_income_category_does_not_block_a_delete(pg):
    mine = database.add_user_category(pg.a, EXPENSE, "Хобби")
    income = database.add_user_category(pg.a, INCOME, "Хобби")
    database.add_budget(pg.b, "Хобби", 100)
    database.delete_user_category(mine["id"], pg.a)
    database.add_budget(pg.a, "Хобби", 100)     # an expense budget of the same name
    database.delete_user_category(income["id"], pg.a)
    assert stored(pg.a) == [] and budget_categories(pg.a) == ["Хобби"] and budget_categories(pg.b) == ["Хобби"]


# --- Concurrency -----------------------------------------------------------------------------------

def test_concurrent_adds_of_the_same_name_create_one_category(pg):
    names = ["Хобби", "хобби", "ХОББИ", "Хобби ", " хОбБи", "Хобби", "хобби", "ХОББИ"]
    results = run_in_parallel(len(names), lambda index: database.add_user_category(pg.a, EXPENSE, names[index]))
    assert [kind for kind, _ in results].count("ok") == 1, results
    for kind, value in results:
        assert kind == "ok" or isinstance(value, database.CategoryExists), value
    assert len(stored(pg.a)) == 1


def test_concurrent_adds_never_pass_the_limit(pg):
    for index in range(database.MAX_USER_CATEGORIES - 2):
        insert_category(pg.a, EXPENSE if index % 2 else INCOME, "Своя %d" % index)
    results = run_in_parallel(6, lambda index: database.add_user_category(pg.a, EXPENSE, "Новая %d" % index))
    assert [kind for kind, _ in results].count("ok") == 2, results
    for kind, value in results:
        assert kind == "ok" or isinstance(value, database.InvalidInput), value
    assert len(stored(pg.a)) == database.MAX_USER_CATEGORIES


def test_concurrent_renames_to_the_same_name_leave_one_category_with_it(pg):
    ids = [database.add_user_category(pg.a, EXPENSE, "Своя %d" % index)["id"] for index in range(4)]
    results = run_in_parallel(len(ids), lambda index: database.rename_user_category(ids[index], pg.a, "Общая"))
    assert [kind for kind, _ in results].count("ok") == 1, results
    for kind, value in results:
        assert kind == "ok" or isinstance(value, database.CategoryExists), value
    assert [name for _, name in stored(pg.a)].count("Общая") == 1
