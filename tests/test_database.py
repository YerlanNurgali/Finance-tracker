from decimal import Decimal
import pytest
import database


USER_ID = 1


class Cursor:
    def __init__(self, store): self.store = store; self.rowcount = 0; self.result = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, params=()):
        q = " ".join(query.split()).upper()
        if q.startswith("CREATE TABLE") or q.startswith("CREATE INDEX") or "DO $$" in q or "ALTER TABLE" in q:
            return
        if q.startswith("INSERT"):
            # params: (user_id, date, operation_type, amount, category)
            self.store.append((len(self.store) + 1, params[0], params[1], params[2], Decimal(str(params[3])), params[4]))
            self.rowcount = 1
        elif q.startswith("SELECT"):
            # filter by user_id (last param)
            uid = params[-1] if params else None
            self.result = [row for row in self.store if uid is None or row[1] == uid]
        elif q.startswith("DELETE"):
            # params: (operation_id, user_id)
            before = len(self.store)
            self.store[:] = [row for row in self.store if not (row[0] == params[0] and row[1] == params[1])]
            self.rowcount = int(len(self.store) != before)
        elif q.startswith("UPDATE"):
            # params: (date, operation_type, amount, category, operation_id, user_id)
            self.rowcount = 0
            for i, row in enumerate(self.store):
                if row[0] == params[4] and row[1] == params[5]:
                    self.store[i] = (row[0], row[1], params[0], params[1], Decimal(str(params[2])), params[3])
                    self.rowcount = 1
    def fetchall(self): return self.result


class Connection:
    def __init__(self, store): self.store = store; self.closed = 0
    def cursor(self): return Cursor(self.store)
    def commit(self): pass
    def rollback(self): pass
    def close(self): self.closed = 1


@pytest.fixture
def store(monkeypatch):
    data = []
    monkeypatch.setattr(database, "get_connection", lambda: Connection(data))
    return data


def test_crud_and_categories(store):
    database.create_database()
    database.add_operation(USER_ID, "25.08.2026 20:00", "Доход", "5 000", "Зарплата")
    database.add_operation(USER_ID, "25.08.2026 21:00", "Расход", 1500, "Еда")
    rows = database.get_operations(USER_ID)
    assert rows[0][4] == Decimal("5000") and rows[1][5] == "Еда"
    database.update_operation(rows[1][0], USER_ID, "25.08.2026 22:00", "Расход", 2000, "Дом")
    assert database.get_operations(USER_ID)[1][5] == "Дом"
    database.delete_operation(rows[0][0], USER_ID)
    assert len(database.get_operations(USER_ID)) == 1


def test_update_does_not_change_other_operations(store):
    database.add_operation(USER_ID, "date-1", "Доход", 10000, "Зарплата")
    database.add_operation(USER_ID, "date-2", "Расход", 2000, "Еда")
    first, second = database.get_operations(USER_ID)

    database.update_operation(first[0], USER_ID, "date-updated", "Доход", 15000, "Бизнес")

    rows = database.get_operations(USER_ID)
    assert rows[0] == (first[0], USER_ID, "date-updated", "Доход", Decimal("15000"), "Бизнес")
    assert rows[1] == second


def test_delete_and_update_unknown_id_raise(store):
    with pytest.raises(database.OperationNotFound):
        database.delete_operation(999, USER_ID)
    with pytest.raises(database.OperationNotFound):
        database.update_operation(999, USER_ID, "date", "Доход", 100)


def test_update_validates_amount_type_and_category(store):
    database.add_operation(USER_ID, "date", "Доход", 100)
    operation_id = database.get_operations(USER_ID)[0][0]
    with pytest.raises(ValueError, match="больше нуля"):
        database.update_operation(operation_id, USER_ID, "date", "Доход", -1)
    with pytest.raises(ValueError, match="Недопустимый тип"):
        database.update_operation(operation_id, USER_ID, "date", "Неверный", 1)
    with pytest.raises(ValueError, match="нужна категория"):
        database.update_operation(operation_id, USER_ID, "date", "Расход", 1)


@pytest.mark.parametrize("operation_type, amount, category", [("Что-то", 1, None), ("Доход", 0, None), ("Расход", 1, None), ("Расход", 1, " ")])
def test_validation(store, operation_type, amount, category):
    with pytest.raises(ValueError):
        database.add_operation(USER_ID, "date", operation_type, amount, category)


def test_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        database.get_connection()


def test_connections_close_on_read_and_write(store, monkeypatch):
    connections = []
    monkeypatch.setattr(database, "get_connection", lambda: connections.append(Connection(store)) or connections[-1])
    database.add_operation(USER_ID, "date", "Доход", 10)
    database.get_operations(USER_ID)
    assert all(connection.closed for connection in connections)


def test_transaction_rolls_back_and_closes_on_database_error(monkeypatch):
    class FailingCursor(Cursor):
        def execute(self, query, params=()):
            if "INSERT" in query.upper():
                raise RuntimeError("database failure")
            return super().execute(query, params)

    class FailingConnection(Connection):
        def __init__(self):
            super().__init__([])
            self.rolled_back = False

        def cursor(self):
            return FailingCursor(self.store)

        def rollback(self):
            self.rolled_back = True

    connection = FailingConnection()
    monkeypatch.setattr(database, "get_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="database failure"):
        database.add_operation(USER_ID, "date", "Доход", 10)
    assert connection.rolled_back is True
    assert connection.closed == 1
