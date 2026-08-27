import database
from storage import load_operations_from_database


def test_load_operations_from_database(tmp_path, monkeypatch):
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
        2500,
        "Еда"
    )

    operations, balance = load_operations_from_database()

    assert operations == [
        "26.08.2026 10:00 | Доход: +10000 тенге",
        "26.08.2026 11:00 | Расход: -2500 тенге | Категория: Еда",
    ]

    assert balance == 7500

from storage import save_operation_to_database


def test_save_operation_to_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setattr(database, "DB_NAME", str(db_path))

    database.create_database()

    save_operation_to_database(
        "26.08.2026 12:00 | Доход: +15000 тенге"
    )

    save_operation_to_database(
        "26.08.2026 13:00 | Расход: -3000 тенге | Категория: Еда"
    )

    result = database.get_operations()

    assert len(result) == 2

    assert result[0][1] == "26.08.2026 12:00"
    assert result[0][2] == "Доход"
    assert result[0][3] == 15000
    assert result[0][4] is None

    assert result[1][1] == "26.08.2026 13:00"
    assert result[1][2] == "Расход"
    assert result[1][3] == 3000
    assert result[1][4] == "Еда"


from storage import delete_operation_by_id


def test_delete_operation_by_id_from_storage(tmp_path, monkeypatch):
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

    delete_operation_by_id(first_id)

    result = database.get_operations()

    assert len(result) == 1
    assert result[0][0] != first_id
    assert result[0][2] == "Расход"
    assert result[0][3] == 2000


from storage import update_operation_by_id


def test_update_operation_by_id_from_storage(tmp_path, monkeypatch):
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

    update_operation_by_id(
        operation_id,
        "26.08.2026 12:00",
        "Расход",
        3000,
        "Еда"
    )

    result = database.get_operations()

    assert len(result) == 1
    assert result[0][0] == operation_id
    assert result[0][1] == "26.08.2026 12:00"
    assert result[0][2] == "Расход"
    assert result[0][3] == 3000
    assert result[0][4] == "Еда"
