from datetime import datetime

from storage import (
    save_operation_to_database,
    delete_operation_by_id,
    update_operation_by_id,
    get_operation_id_by_position
)


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

    print("\nВыберите категорию дохода:")
    print("1. Зарплата")
    print("2. Фриланс")
    print("3. Бизнес")
    print("4. Инвестиции")
    print("5. Другое")

    income_categories = {
        "1": "Зарплата",
        "2": "Фриланс",
        "3": "Бизнес",
        "4": "Инвестиции",
        "5": "Другое",
    }

    while True:
        choice = input("Ваш выбор: ")

        if choice in income_categories:
            category = income_categories[choice]
            break

        print("Ошибка: выберите число от 1 до 5.")

    balance += income

    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    operation = (
        f"{date} | Доход: +{format_money(income)} тенге "
        f"| Категория: {category}"
    )

    operations.append(operation)
    save_operation_to_database(operation)
    print("Доход добавлен!")

    return balance


def test_add_income_with_category():
    operations = []
    balance = 0

    with patch("builtins.input", side_effect=["5000", "1"]), \
         patch("operations.save_operation_to_database") as mock_save:

        new_balance = add_income(balance, operations)

    assert new_balance == 5000
    assert len(operations) == 1
    assert "Доход: +5000" in operations[0]
    assert "Категория: Зарплата" in operations[0]

    mock_save.assert_called_once_with(operations[0])

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
    save_operation_to_database(operation)
    print("Расход добавлен!")

    return balance

def edit_operation(operations):
    print("\n--- РЕДАКТИРОВАНИЕ ОПЕРАЦИИ ---")

    if not operations:
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

        operation_id = get_operation_id_by_position(choice - 1)

        if operation_id is None:
            print("Ошибка: операция не найдена в базе данных.")
            return

        print("\nТекущая операция:")
        print(old_operation)

        new_amount = get_amount("Введите новую сумму: ")

        parts = [part.strip() for part in old_operation.split("|")]

        category = None

        if len(parts) > 1:
            date = parts[0]
            operation_data = parts[1]

            if operation_data.startswith("Доход:"):
                operations[choice - 1] = (
                    f"{date} | Доход: +{format_money(new_amount)} тенге"
                )

            elif operation_data.startswith("Расход:"):
                if len(parts) > 2 and parts[2].startswith("Категория:"):
                    category = parts[2].replace("Категория:", "").strip()

                print(f"\nТекущая категория: {category}")
                print("Нажмите Enter, чтобы оставить её.")
                print("Или выберите новую категорию:")
                print("1. Еда")
                print("2. Транспорт")
                print("3. Дом")
                print("4. Развлечения")
                print("5. Здоровье")
                print("6. Другое")

                new_category = input("Новая категория (1-6): ").strip()

                if new_category:
                    categories = {
                        "1": "Еда",
                        "2": "Транспорт",
                        "3": "Дом",
                        "4": "Развлечения",
                        "5": "Здоровье",
                        "6": "Другое",
                    }

                    if new_category in categories:
                        category = categories[new_category]
                    else:
                        print("Ошибка: категория не изменена.")

                operations[choice - 1] = (
                    f"{date} | Расход: -{format_money(new_amount)} тенге"
                    f" | Категория: {category}"
                )

        update_operation_by_id(
            operation_id,
            date,
            "Доход" if operation_data.startswith("Доход:") else "Расход",
            new_amount,
            category
        )

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

        deleted_operation = operations[choice - 1]

        operation_id = get_operation_id_by_position(choice - 1)

        if operation_id is None:
            print("Ошибка: операция не найдена в базе данных.")
            return

        delete_operation_by_id(operation_id)

        operations.pop(choice - 1)

        print(f"Операция удалена: {deleted_operation}")

    except ValueError:
        print("Ошибка: введите номер операции.")
