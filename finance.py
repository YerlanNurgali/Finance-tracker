from utils import parse_operation, format_money


def calculate_balance(operations):
    balance = 0

    for operation in operations:
        operation_type, amount, category = parse_operation(operation)

        if operation_type == "Доход":
            balance += amount

        elif operation_type == "Расход":
            balance -= amount

    return balance


def show_statistics(operations):
    total_income = 0
    total_expense = 0
    categories = {}

    for operation in operations:
        operation_type, amount, category = parse_operation(operation)

        if operation_type == "Доход":
            total_income += amount

        elif operation_type == "Расход":
            total_expense += amount

            if category:
                if category not in categories:
                    categories[category] = 0

                categories[category] += amount

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
        operation_type, amount, category = parse_operation(operation)
        if operation_type == "Расход" and category:
            categories[category] = categories.get(category, 0) + amount

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
