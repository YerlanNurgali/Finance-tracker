
from operations import (
    add_income,
    add_expense,
    choose_category,
    edit_operation,
    delete_operation,
)

from utils import format_money, parse_operation

from finance import (
    calculate_balance,
    show_statistics,
    get_category_expenses,
    show_category_chart,
)

from storage import load_operations_from_database

def show_balance(balance):
    print(f"Ваш баланс: {format_money(balance)} тенге")

def format_operation(operation):
    operation_type, amount, category = parse_operation(operation)
    if operation_type is None:
        return operation
    prefix = f"{operation.split('|', 1)[0].strip()} | " if "|" in operation else ""
    sign = "+" if operation_type == "Доход" else "-"
    result = f"{prefix}{operation_type}: {sign}{format_money(amount)} тенге"
    return f"{result} | Категория: {category}" if category else result


def show_history(operations):
    print("\n--- ИСТОРИЯ ОПЕРАЦИЙ ---")

    if len(operations) == 0:
        print("Операций пока нет.")
    else:
        for number, operation in enumerate(operations, start=1):
            print(f"{number}. {format_operation(operation)}")

def main():
    operations, balance = load_operations_from_database()

    while True:
        print("1. Добавить доход")
        print("2. Добавить расход")
        print("3. Показать баланс")
        print("4. Показать историю")
        print("5. Показать статистику")
        print("6. Показать диаграмму")
        print("7. Удалить операцию")
        print("8. Редактировать операцию")
        print("9. Выход")
        
        choice = input("Выберите действие: ")

        if choice == "1":
            balance = add_income(balance, operations)

        elif choice == "2":
            balance = add_expense(balance, operations)

        elif choice == "3":
            show_balance(balance)

        elif choice == "4":
            show_history(operations)

        elif choice == "5":
            show_statistics(operations)

        elif choice == "6":
            show_category_chart(operations)

        elif choice == "7":
            delete_operation(operations)
            balance = calculate_balance(operations)

        elif choice == "8":
            edit_operation(operations)
            balance = calculate_balance(operations)

        elif choice == "9":
            print("До свидания!")
            break


        else:
            print("Неверный выбор. Попробуйте ещё раз.")


if __name__ == "__main__":
    main()
