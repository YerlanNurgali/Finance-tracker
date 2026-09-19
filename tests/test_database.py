import pytest

import database


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


def test_add_and_get_operation():
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


def test_delete_operation():
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


def test_update_operation():
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


def test_create_database():
    database.create_database()

    connection = database.get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'operations_test'
        )
        """
    )

    result = cursor.fetchone()[0]

    connection.close()

    assert result is True


def test_add_expense_with_category():
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


def test_add_operation_rejects_negative_amount():
    with pytest.raises(ValueError):
        database.add_operation(
            "25.08.2026 20:00",
            "Доход",
            -5000
        )


def test_add_operation_rejects_invalid_type():
    with pytest.raises(ValueError):
        database.add_operation(
            "25.08.2026 20:00",
            "Что-то",
            5000
        )


def test_expense_requires_category():
    with pytest.raises(ValueError):
        database.add_operation(
            "25.08.2026 20:00",
            "Расход",
            2000
        )


def test_income_can_be_without_category():
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


def test_delete_first_operation_by_id():
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


def test_delete_operation_by_id():
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


def test_update_operation_by_id():
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


def test_update_operation_does_not_change_other_operations():
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


def test_update_operation_rejects_negative_amount():
    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    with pytest.raises(ValueError, match="Сумма должна быть больше нуля"):
        database.update_operation(
            operation_id,
            "26.08.2026 12:00",
            "Доход",
            -5000
        )


def test_update_operation_rejects_invalid_type():
    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    with pytest.raises(ValueError, match="Недопустимый тип операции"):
        database.update_operation(
            operation_id,
            "26.08.2026 12:00",
            "Что-то",
            5000
        )


def test_update_expense_requires_category():
    database.add_operation(
        "26.08.2026 10:00",
        "Доход",
        10000
    )

    operations = database.get_operations()
    operation_id = operations[0][0]

    with pytest.raises(ValueError, match="Для расхода нужна категория"):
        database.update_operation(
            operation_id,
            "26.08.2026 12:00",
            "Расход",
            2000
        )
