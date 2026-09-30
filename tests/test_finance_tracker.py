from unittest.mock import patch

from finance import calculate_balance, get_category_expenses
from main import format_operation
from operations import add_expense, add_income, choose_category, edit_operation
from utils import format_money, parse_operation


def test_parse_formatted_amount_and_category():
    assert parse_operation("date | Расход: -5 000 тенге | Категория: Еда") == ("Расход", 5000.0, "Еда")


def test_money_format_and_finance():
    operations = ["Доход: +10 000 тенге", "date | Расход: -2 500 тенге | Категория: Еда"]
    assert format_money(5000) == "5 000"
    assert calculate_balance(operations) == 7500
    assert get_category_expenses(operations) == {"Еда": 2500.0}
    assert format_operation("date | Расход: -5 000 тенге | Категория: Еда") == "date | Расход: -5 000 тенге | Категория: Еда"


def test_income_and_expense_categories():
    operations = []
    with patch("builtins.input", side_effect=["5 000", "1"]), patch("operations.save_operation_to_database"):
        assert add_income(0, operations) == 5000
    with patch("builtins.input", side_effect=["1 500", "1"]), patch("operations.save_operation_to_database"):
        assert add_expense(5000, operations) == 3500
    assert "Категория: Зарплата" in operations[0]
    assert "Категория: Еда" in operations[1]


def test_expense_is_rejected_when_balance_is_insufficient():
    operations = []
    with patch("builtins.input", return_value="1500"), patch("operations.save_operation_to_database") as save:
        assert add_expense(1000, operations) == 1000
    assert operations == []
    save.assert_not_called()


def test_category_retries_invalid_choice():
    with patch("builtins.input", side_effect=["9", "2"]):
        assert choose_category() == "Транспорт"


def test_edit_keeps_category():
    operations = ["date | Расход: -1 500 тенге | Категория: Транспорт"]
    with patch("builtins.input", side_effect=["1", "2000", ""]), patch("operations.get_operation_id_by_position", return_value=1), patch("operations.update_operation_by_id") as update:
        edit_operation(operations)
    assert "-2 000" in operations[0] and "Транспорт" in operations[0]
    update.assert_called_once_with(1, "date", "Расход", 2000, "Транспорт")


def test_edit_changes_expense_category():
    operations = ["date | Расход: -1 500 тенге | Категория: Транспорт"]
    with patch("builtins.input", side_effect=["1", "2000", "4"]), patch("operations.get_operation_id_by_position", return_value=1), patch("operations.update_operation_by_id") as update:
        edit_operation(operations)
    assert operations[0].endswith("Категория: Развлечения")
    update.assert_called_once_with(1, "date", "Расход", 2000, "Развлечения")


def test_edit_preserves_income_category():
    operations = ["date | Доход: +10 000 тенге | Категория: Зарплата"]
    with patch("builtins.input", side_effect=["1", "12000"]), patch("operations.get_operation_id_by_position", return_value=1), patch("operations.update_operation_by_id") as update:
        edit_operation(operations)
    assert operations[0].endswith("Категория: Зарплата")
    update.assert_called_once_with(1, "date", "Доход", 12000, "Зарплата")


def test_balance_after_delete_and_update():
    operations = [
        "date | Доход: +10 000 тенге | Категория: Зарплата",
        "date | Расход: -3 000 тенге | Категория: Еда",
    ]
    from finance import calculate_balance

    assert calculate_balance(operations) == 7000
    operations[1] = "date | Расход: -2 000 тенге | Категория: Еда"
    assert calculate_balance(operations) == 8000
    operations.pop(1)
    assert calculate_balance(operations) == 10000
