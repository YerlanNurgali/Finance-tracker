import database
from storage import (
    delete_operation_by_id,
    get_operation_id_by_position,
    load_operations_from_database,
    save_operation_to_database,
    update_operation_by_id,
)


def test_storage_round_trip(monkeypatch):
    rows = [
        (1, "26.08.2026 10:00", "Доход", 10000, "Зарплата"),
        (2, "26.08.2026 11:00", "Расход", 2500, "Еда"),
    ]
    monkeypatch.setattr("storage.get_operations", lambda: rows)
    operations, balance = load_operations_from_database()
    assert "Категория: Зарплата" in operations[0]
    assert balance == 7500


def test_save_operation_preserves_spaces_and_category(monkeypatch):
    calls = []
    monkeypatch.setattr("storage.add_operation", lambda *args: calls.append(args))
    save_operation_to_database("date | Расход: -5 000 тенге | Категория: Дом")
    assert calls == [("date", "Расход", 5000.0, "Дом")]


def test_storage_delete_update_and_position_delegate(monkeypatch):
    deleted = []
    updated = []
    rows = [(41, "date", "Доход", 100, "Зарплата"), (42, "date-2", "Расход", 20, "Еда")]
    monkeypatch.setattr("storage.delete_operation", lambda operation_id: deleted.append(operation_id))
    monkeypatch.setattr("storage.update_operation", lambda *args: updated.append(args))
    monkeypatch.setattr("storage.get_operations", lambda: rows)

    delete_operation_by_id(41)
    update_operation_by_id(42, "new-date", "Расход", 25, "Дом")

    assert deleted == [41]
    assert updated == [(42, "new-date", "Расход", 25, "Дом")]
    assert get_operation_id_by_position(0) == 41
    assert get_operation_id_by_position(1) == 42
    assert get_operation_id_by_position(-1) is None
    assert get_operation_id_by_position(2) is None
