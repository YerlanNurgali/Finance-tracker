from datetime import datetime

from utils import format_money, parse_operation, get_amount

from finance import (
    calculate_balance,
    show_statistics,
    get_category_expenses,
    show_category_chart,
)

from storage import load_operations, save_operation, save_operations

operations, balance = load_operations()


def add_income(balance, operations):
    income = get_amount("Введите сумму дохода: ")

    balance += income

    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    operation = f"{date} | Доход: +{income} тенге"

    operations.append(operation)
    save_operation(operation)

    print("Доход добавлен!")

    return balance



def choose_category():
    print("\nВыберите категорию:")
    print("1. Еда")
    print("2. Транспорт")
    print("3. Дом")
    print("4. Развлечения")
    print("5. Здоровье")
    print("6. Другое")

    categories = {
        "1": "Еда",
        "2": "Транспорт",
        "3": "Дом",
        "4": "Развлечения",
        "5": "Здоровье",
        "6": "Другое"
    }

    while True:
        choice = input("Ваш выбор: ")

        if choice in categories:
            return categories[choice]

        print("Ошибка: выберите число от 1 до 6.")

def add_expense(balance, operations):

    expense = get_amount("Введите сумму расхода: ")

    if expense > balance:
        print("Ошибка: недостаточно средств.")
        return balance

    balance -= expense

    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    category = choose_category()
    operation = f"{date} | Расход: -{expense} тенге | Категория: {category}"
        
    operations.append(operation)
    save_operation(operation)

    print("Расход добавлен!")

    return balance


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


def edit_operation(operations):
    print("\n--- РЕДАКТИРОВАНИЕ ОПЕРАЦИИ ---")

    if len(operations) == 0:
        print("Операций пока нет.")
        return

    for number, operation in enumerate(operations, start=1):
        print(f"{number}. {format_operation(operation)}")

    try:
        choice = int(input("Введите номер операции для редактирования: "))

        if choice < 1 or choice > len(operations):
            print("Ошибка: такой операции нет.")
            return

        old_operation = operations[choice - 1]

        print("\nТекущая операция:")
        print(format_operation(old_operation))

        new_amount = get_amount("Введите новую сумму: ")

        parts = [part.strip() for part in old_operation.split("|")]

        if len(parts) > 1:
            date = parts[0]
            operation_data = parts[1]

            if operation_data.startswith("Доход:"):
                operations[choice - 1] = (
                    f"{date} | Доход: +{format_money(new_amount)} тенге"
                )

            elif operation_data.startswith("Расход:"):
                category = choose_category()

                operations[choice - 1] = (
                    f"{date} | Расход: -{format_money(new_amount)} тенге"
                    f" | Категория: {category}"
                )

        else:
            if old_operation.startswith("Доход:"):
                operations[choice - 1] = (
                    f"Доход: +{format_money(new_amount)} тенге"
                )

            elif old_operation.startswith("Расход:"):
                operations[choice - 1] = (
                    f"Расход: -{format_money(new_amount)} тенге"
                )

        save_operations(operations)

        print("Операция изменена!")

    except ValueError:
        print("Ошибка: введите число.")

def delete_operation(operations):
    print("\n--- УДАЛЕНИЕ ОПЕРАЦИИ ---")

    if len(operations) == 0:
        print("Операций пока нет.")
        return

    for number, operation in enumerate(operations, start=1):
        print(f"{number}. {format_operation(operation)}")

    try:
        choice = int(input("Введите номер операции для удаления: "))

        if choice < 1 or choice > len(operations):
            print("Ошибка: такой операции нет.")
            return

        deleted_operation = operations.pop(choice - 1)

        save_operations(operations)

        print(f"Операция удалена: {format_operation(deleted_operation)}")

    except ValueError:
        print("Ошибка: введите номер операции.")

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
