import sqlite3

import database


def test_add_and_get_operation(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Доход",
        10000
    )

    operations = database.get_operations()

    assert len(operations) == 1
    assert operations[0][1] == "25.08.2026 20:00"
    assert operations[0][2] == "Доход"
    assert operations[0][3] == 10000
    assert operations[0][4] is None


def test_delete_operation(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Расход",
        2000,
        "Еда"
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    database.delete_operation(operation_id)

    assert database.get_operations() == []


def test_update_operation(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Расход",
        2000,
        "Еда"
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    database.update_operation(
        operation_id,
        "25.08.2026 20:30",
        "Расход",
        3500,
        "Развлечения"
    )

    updated = database.get_operations()

    assert updated[0][1] == "25.08.2026 20:30"
    assert updated[0][2] == "Расход"
    assert updated[0][3] == 3500
    assert updated[0][4] == "Развлечения"


def test_create_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operations'"
    )

    result = cursor.fetchone()

    connection.close()

    assert result == ("operations",)


def test_add_expense_with_category(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Расход",
        3500,
        "Развлечения"
    )

    operations = database.get_operations()

    assert operations[0][2] == "Расход"
    assert operations[0][3] == 3500
    assert operations[0][4] == "Развлечения"

def test_add_operation_rejects_negative_amount(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    try:
        database.add_operation(
            "25.08.2026 20:00",
            "Доход",
            -5000
        )
    except ValueError:
        pass
    else:
        assert False, "Отрицательная сумма должна вызывать ValueError"

def test_add_operation_rejects_invalid_type(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    try:
        database.add_operation(
            "25.08.2026 20:00",
            "Что-то",
            5000
        )
    except ValueError:
        pass
    else:
        assert False, "Недопустимый тип операции должен вызывать ValueError"


def test_expense_requires_category(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    try:
        database.add_operation(
            "25.08.2026 20:00",
            "Расход",
            2000
        )
    except ValueError:
        pass
    else:
        assert False, "Для расхода категория обязательна"


def test_income_can_be_without_category(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Доход",
        5000
    )

    operations = database.get_operations()

    assert len(operations) == 1
    assert operations[0][2] == "Доход"
    assert operations[0][3] == 5000
    assert operations[0][4] is None

def test_delete_operation_by_id(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "25.08.2026 20:00",
        "Доход",
        5000
    )

    database.add_operation(
        "25.08.2026 20:01",
        "Расход",
        1500,
        "Еда"
    )

    operations = database.get_operations()

    first_id = operations[0][0]
    second_id = operations[1][0]

    database.delete_operation(second_id)

    result = database.get_operations()

    assert len(result) == 1
    assert result[0][0] == first_id

def test_delete_operation_by_id(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    database.add_operation(
        "26.08.2026 11:00",
        "Расход",
        2000,
        "Еда"
    )

    operations = database.get_operations()

    assert len(operations) == 2

    first_operation_id = operations[0][0]

    database.delete_operation(first_operation_id)

    operations = database.get_operations()

    assert len(operations) == 1
    assert operations[0][2] == "Расход"
    assert operations[0][3] == 2000

def test_update_operation_by_id(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    database.update_operation(
        operation_id,
        "26.08.2026 12:00",
        "Расход",
        2500,
        "Еда"
    )

    result = database.get_operations()

    assert len(result) == 1
    assert result[0][0] == operation_id
    assert result[0][1] == "26.08.2026 12:00"
    assert result[0][2] == "Расход"
    assert result[0][3] == 2500
    assert result[0][4] == "Еда"


def test_update_operation_does_not_change_other_operations(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    database.add_operation(
        "26.08.2026 11:00",
        "Расход",
        2000,
        "Еда"
    )

    operations = database.get_operations()

    first_id = operations[0][0]
    second_id = operations[1][0]

    database.update_operation(
        first_id,
        "26.08.2026 12:00",
        "Доход",
        15000
    )

    result = database.get_operations()

    assert len(result) == 2

    assert result[0][0] == first_id
    assert result[0][3] == 15000

    assert result[1][0] == second_id
    assert result[1][1] == "26.08.2026 11:00"
    assert result[1][2] == "Расход"
    assert result[1][3] == 2000
    assert result[1][4] == "Еда"
