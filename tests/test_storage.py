import pytest

import database

from storage import (
    load_operations_from_database,
    save_operation_to_database,
    delete_operation_by_id,
    update_operation_by_id,
    get_operation_id_by_position
)


@pytest.fixture(autouse=True)
def setup_test_database(monkeypatch):
    monkeypatch.setattr(database, "TABLE_NAME", "operations_test")

    database.create_database()

    connection = database.get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "TRUNCATE TABLE operations_test RESTART IDENTITY"
    )

    connection.commit()
    connection.close()


def test_load_operations_from_database():
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


def test_save_operation_to_database():
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


def test_delete_operation_by_id_from_storage():
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


def test_update_operation_by_id_from_storage():
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


def test_get_operation_id_by_position():
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

    assert get_operation_id_by_position(0) == first_id
    assert get_operation_id_by_position(1) == second_id
    assert get_operation_id_by_position(2) is None
    assert get_operation_id_by_position(-1) is None
