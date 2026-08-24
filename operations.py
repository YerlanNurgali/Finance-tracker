from datetime import datetime

from storage import save_operation, save_operations

from utils import get_amount, format_money

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
        "6": "Другое",
    }

    while True:
        choice = input("Ваш выбор: ")

        if choice in categories:
            return categories[choice]

        print("Ошибка: выберите число от 1 до 6.")


def add_income(balance, operations):
    income = get_amount("Введите сумму дохода: ")

    balance += income

    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    operation = f"{date} | Доход: +{income} тенге"

    operations.append(operation)
    save_operation(operation)

    print("Доход добавлен!")

    return balance


def add_expense(balance, operations):
    expense = get_amount("Введите сумму расхода: ")

    if expense > balance:
        print("Ошибка: недостаточно средств.")
        return balance

    balance -= expense

    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    category = choose_category()

    operation = (
        f"{date} | Расход: -{expense} тенге "
        f"| Категория: {category}"
    )

    operations.append(operation)
    save_operation(operation)

    print("Расход добавлен!")

    return balance

def edit_operation(operations):
    print("\n--- РЕДАКТИРОВАНИЕ ОПЕРАЦИИ ---")

    if len(operations) == 0:
        print("Операций пока нет.")
        return

    for number, operation in enumerate(operations, start=1):
        print(f"{number}. {operation}")

    try:
        choice = int(input("Введите номер операции для редактирования: "))

        if choice < 1 or choice > len(operations):
            print("Ошибка: такой операции нет.")
            return

        old_operation = operations[choice - 1]

        print("\nТекущая операция:")
        print(old_operation)

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
        print(f"{number}. {operation}")

    try:
        choice = int(input("Введите номер операции для удаления: "))

        if choice < 1 or choice > len(operations):
            print("Ошибка: такой операции нет.")
            return

        deleted_operation = operations.pop(choice - 1)

        save_operations(operations)

        print(f"Операция удалена: {deleted_operation}")

    except ValueError:
        print("Ошибка: введите номер операции.")
