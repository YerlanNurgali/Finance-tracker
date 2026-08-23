from datetime import datetime

from storage import load_operations, save_operation, save_operations

operations, balance = load_operations()

def add_income(balance, operations):
    try:
        income = float(input("Введите сумму дохода: "))

        if income <= 0:
            print("Ошибка: сумма должна быть больше 0.")
            return balance

        balance += income

        date = datetime.now().strftime("%d.%m.%Y %H:%M")
        operation = f"{date} | Доход: +{income} тенге"

        operations.append(operation)
        save_operation(operation)

        print("Доход добавлен!")

        return balance

    except ValueError:
        print("Ошибка: введите число.")
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
    try:
        expense = float(input("Введите сумму расхода: "))

        if expense <= 0:
            print("Ошибка: сумма должна быть больше 0.")
            return balance

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

    except ValueError:
        print("Ошибка: введите число.")
        return balance

def format_money(amount):
    return f"{amount:,.0f}".replace(",", " ")

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

def show_statistics(operations):
    total_income = 0
    total_expense = 0
    categories = {}

    for operation in operations:
        if "|" in operation:
            parts = [part.strip() for part in operation.split("|")]

            operation_data = parts[1]

            if operation_data.startswith("Доход:"):
                amount = float(operation_data.split("+")[1].split(" тенге")[0])
                total_income += amount

            elif operation_data.startswith("Расход:"):
                amount = float(operation_data.split("-")[1].split(" тенге")[0])
                total_expense += amount

                if len(parts) > 2 and parts[2].startswith("Категория:"):
                    category = parts[2].replace("Категория:", "").strip()

                    if category not in categories:
                        categories[category] = 0

                    categories[category] += amount

        else:
            if operation.startswith("Доход:"):
                amount = float(operation.split("+")[1].split(" тенге")[0])
                total_income += amount

            elif operation.startswith("Расход:"):
                amount = float(operation.split("-")[1].split(" тенге")[0])
                total_expense += amount

    print("\n--- СТАТИСТИКА ---")
    print(f"Всего доходов: {format_money(total_income)} тенге")
    print(f"Всего расходов: {format_money(total_expense)} тенге")

    balance = total_income - total_expense

    print(f"Остаток: {format_money(balance)} тенге")
    print(f"Всего операций: {len(operations)}")

    print("\nРасходы по категориям:")

    if len(categories) == 0:
        print("Категорий пока нет.")
    else:
        for category, amount in categories.items():
            if total_expense > 0:
                percentage = amount / total_expense * 100
            else:
                percentage = 0

            print(
                f"{category}: {format_money(amount)} тенге "
                f"({percentage:.1f}%)"
        )

def get_category_expenses(operations):
    categories = {}

    for operation in operations:
        if "|" not in operation:
            continue

        parts = [part.strip() for part in operation.split("|")]

        if len(parts) < 2:
            continue

        operation_data = parts[1]

        if not operation_data.startswith("Расход:"):
            continue

        amount = float(operation_data.split("-")[1].split(" тенге")[0])

        if len(parts) > 2 and parts[2].startswith("Категория:"):
            category = parts[2].replace("Категория:", "").strip()

            if category not in categories:
                categories[category] = 0

            categories[category] += amount

    return categories

def show_category_chart(operations):
    categories = get_category_expenses(operations)

    print("\n--- РАСХОДЫ ПО КАТЕГОРИЯМ ---")

    if len(categories) == 0:
        print("Категорий пока нет.")
        return

    max_amount = max(categories.values())

    for category, amount in categories.items():
        bar_length = int(amount / max_amount * 20)

        bar = "█" * bar_length

        print(
            f"{category:<12} {bar} "
            f"{format_money(amount)} тенге"
        )

def calculate_balance(operations):
    balance = 0

    for operation in operations:
        if "|" in operation:
            operation_data = operation.split("|", 1)[1].strip()
        else:
            operation_data = operation

        if operation_data.startswith("Доход:"):
            amount = float(
                operation_data.split("+")[1].split(" тенге")[0]
            )
            balance += amount

        elif operation_data.startswith("Расход:"):
            amount = float(
                operation_data.split("-")[1].split(" тенге")[0]
            )
            balance -= amount

    return balance

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

        print(f"\nТекущая операция:")
        print(format_operation(old_operation))

        new_amount = float(input("Введите новую сумму: "))

        if new_amount <= 0:
            print("Ошибка: сумма должна быть больше 0.")
            return

        if "|" in old_operation:
            parts = old_operation.split("|")

            operation_data = parts[1].strip()

            if operation_data.startswith("Доход:"):
                parts[1] = f" Доход: +{new_amount} тенге"

            elif operation_data.startswith("Расход:"):
                parts[1] = f" Расход: -{new_amount} тенге"

            operations[choice - 1] = "|".join(parts)

        else:
            if old_operation.startswith("Доход:"):
                operations[choice - 1] = f"Доход: +{new_amount} тенге"

            elif old_operation.startswith("Расход:"):
                operations[choice - 1] = f"Расход: -{new_amount} тенге"

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
