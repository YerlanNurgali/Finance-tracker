from main import parse_operation
from main import edit_operation
from main import add_income, add_expense
from unittest.mock import patch
from operations import add_income, add_expense, delete_operation


def test_income():
    operation = "Доход: +15000 тенге"

    result = parse_operation(operation)

    assert result == ("Доход", 15000.0, "")


def test_expense_with_category():
    operation = "24.08.2026 00:42 | Расход: -3500 тенге | Категория: Развлечения"

    result = parse_operation(operation)

    assert result == ("Расход", 3500.0, "Развлечения")

from main import calculate_balance


def test_calculate_balance():
    operations = [
        "24.08.2026 10:00 | Доход: +15000 тенге",
        "24.08.2026 11:00 | Расход: -2000 тенге | Категория: Еда",
        "24.08.2026 12:00 | Расход: -1500 тенге | Категория: Транспорт",
    ]

    result = calculate_balance(operations)

    assert result == 11500.0

from main import get_category_expenses


def test_category_expenses():
    operations = [
        "24.08.2026 10:00 | Расход: -1000 тенге | Категория: Еда",
        "24.08.2026 11:00 | Расход: -2000 тенге | Категория: Транспорт",
        "24.08.2026 12:00 | Расход: -500 тенге | Категория: Еда",
    ]

    result = get_category_expenses(operations)

    assert result == {
        "Еда": 1500.0,
        "Транспорт": 2000.0,
    }

from unittest.mock import patch
from main import edit_operation


def test_edit_operation():
    operations = [
        "24.08.2026 00:42 | Расход: -1500 тенге | Категория: Транспорт"
    ]

    with patch("builtins.input", side_effect=["1", "2000", "4"]):
        edit_operation(operations)

    assert operations[0] == (
        "24.08.2026 00:42 | Расход: -2 000 тенге | Категория: Развлечения"
    )

from unittest.mock import patch
from main import delete_operation


def test_delete_operation():
    operations = [
        "Доход: +10000 тенге",
        "Расход: -2000 тенге",
        "Доход: +5000 тенге",
    ]

    with patch("builtins.input", return_value="2"):
        delete_operation(operations)

    assert operations == [
        "Доход: +10000 тенге",
        "Доход: +5000 тенге",
    ]

def test_parse_operation_with_formatted_amount():
    operation = (
        "24.08.2026 12:00 | "
        "Расход: -1 500 тенге | "
        "Категория: Еда"
    )

    operation_type, amount, category = parse_operation(operation)

    assert operation_type == "Расход"
    assert amount == 1500.0
    assert category == "Еда"

def test_add_income():
    operations = []
    balance = 0

    with patch("builtins.input", side_effect=["5000"]):
        new_balance = add_income(balance, operations)

    assert new_balance == 5000
    assert len(operations) == 1
    assert "Доход: +5000" in operations[0]


def test_add_expense():
    operations = []
    balance = 5000

    with patch("builtins.input", side_effect=["1500", "1"]):
        new_balance = add_expense(balance, operations)

    assert new_balance == 3500
    assert len(operations) == 1
    assert "Расход: -1500" in operations[0]
    assert "Категория: Еда" in operations[0]


def test_expense_more_than_balance():
    operations = []
    balance = 1000

    with patch("builtins.input", side_effect=["1500"]):
        new_balance = add_expense(balance, operations)

    assert new_balance == 1000
    assert len(operations) == 0

print("Все тесты пройдены!")
