from datetime import datetime

from operations import (
    add_income,
    add_expense,
    choose_category,
    edit_operation,
    delete_operation,
)

from utils import format_money, parse_operation, get_amount

from finance import (
    calculate_balance,
    show_statistics,
    get_category_expenses,
    show_category_chart,
)

from storage import load_operations, save_operation, save_operations

operations, balance = load_operations()


def show_balance(balance):
    print(f"Ваш баланс: {format_money(balance)} тенге")

def format_operation(operation):
    if "|" in operation:
        parts = [part.strip() for part in operation.split("|")]

        date = parts[0]
        operation_data = parts[1]

        category = ""

        if len(parts) > 2 and parts[2].startswith("Категория:"):
            category = parts[2]

        if operation_data.startswith("Доход:"):
            amount = float(operation_data.split("+")[1].split(" тенге")[0])
            result = f"{date} | Доход: +{format_money(amount)} тенге"

        elif operation_data.startswith("Расход:"):
            amount = float(operation_data.split("-")[1].split(" тенге")[0])
            result = f"{date} | Расход: -{format_money(amount)} тенге"

        else:
            return operation

        if category:
            result += f" | {category}"

        return result

    if operation.startswith("Доход:"):
        amount = float(operation.split("+")[1].split(" тенге")[0])
        return f"Доход: +{format_money(amount)} тенге"

    elif operation.startswith("Расход:"):
        amount = float(operation.split("-")[1].split(" тенге")[0])
        return f"Расход: -{format_money(amount)} тенге"

    return operation


def show_history(operations):
    print("\n--- ИСТОРИЯ ОПЕРАЦИЙ ---")

    if len(operations) == 0:
        print("Операций пока нет.")
    else:
        for number, operation in enumerate(operations, start=1):
            print(f"{number}. {format_operation(operation)}")

def main():
    operations, balance = load_operations()

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
