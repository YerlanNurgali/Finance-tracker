from decimal import Decimal
import pytest
import database


class Cursor:
    def __init__(self, store): self.store = store; self.rowcount = 0; self.result = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, params=()):
        q = " ".join(query.split()).upper()
        if q.startswith("CREATE TABLE"): return
        if q.startswith("INSERT"):
            self.store.append((len(self.store) + 1, params[0], params[1], Decimal(str(params[2])), params[3])); self.rowcount = 1
        elif q.startswith("SELECT"): self.result = list(self.store)
        elif q.startswith("DELETE"):
            before = len(self.store); self.store[:] = [row for row in self.store if row[0] != params[0]]; self.rowcount = int(len(self.store) != before)
        elif q.startswith("UPDATE"):
            self.rowcount = 0
            for i, row in enumerate(self.store):
                if row[0] == params[4]: self.store[i] = (row[0], params[0], params[1], Decimal(str(params[2])), params[3]); self.rowcount = 1
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
    database.add_operation("25.08.2026 20:00", "Доход", "5 000", "Зарплата")
    database.add_operation("25.08.2026 21:00", "Расход", 1500, "Еда")
    rows = database.get_operations()
    assert rows[0][3] == Decimal("5000") and rows[1][4] == "Еда"
    database.update_operation(rows[1][0], "25.08.2026 22:00", "Расход", 2000, "Дом")
    assert database.get_operations()[1][4] == "Дом"
    database.delete_operation(rows[0][0])
    assert len(database.get_operations()) == 1


def test_update_does_not_change_other_operations(store):
    database.add_operation("date-1", "Доход", 10000, "Зарплата")
    database.add_operation("date-2", "Расход", 2000, "Еда")
    first, second = database.get_operations()

    database.update_operation(first[0], "date-updated", "Доход", 15000, "Бизнес")

    rows = database.get_operations()
    assert rows[0] == (first[0], "date-updated", "Доход", Decimal("15000"), "Бизнес")
    assert rows[1] == second


def test_delete_and_update_unknown_id_raise(store):
    with pytest.raises(database.OperationNotFound):
        database.delete_operation(999)
    with pytest.raises(database.OperationNotFound):
        database.update_operation(999, "date", "Доход", 100)


def test_update_validates_amount_type_and_category(store):
    database.add_operation("date", "Доход", 100)
    operation_id = database.get_operations()[0][0]
    with pytest.raises(ValueError, match="больше нуля"):
        database.update_operation(operation_id, "date", "Доход", -1)
    with pytest.raises(ValueError, match="Недопустимый тип"):
        database.update_operation(operation_id, "date", "Неверный", 1)
    with pytest.raises(ValueError, match="нужна категория"):
        database.update_operation(operation_id, "date", "Расход", 1)


@pytest.mark.parametrize("operation_type, amount, category", [("Что-то", 1, None), ("Доход", 0, None), ("Расход", 1, None), ("Расход", 1, " ")])
def test_validation(store, operation_type, amount, category):
    with pytest.raises(ValueError): database.add_operation("date", operation_type, amount, category)


def test_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"): database.get_connection()


def test_connections_close_on_read_and_write(store, monkeypatch):
    connections = []
    monkeypatch.setattr(database, "get_connection", lambda: connections.append(Connection(store)) or connections[-1])
    database.add_operation("date", "Доход", 10)
    database.get_operations()
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
        database.add_operation("date", "Доход", 10)
    assert connection.rolled_back is True
    assert connection.closed == 1
