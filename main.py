from datetime import datetime

def load_operations():
    operations = []
    balance = 0

    try:
        with open("operations.txt", "r") as file:
            for line in file:
                operation = line.strip()

                if operation:
                    operations.append(operation)

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

    except FileNotFoundError:
        pass

    return operations, balance

operations, balance = load_operations()



def save_operation(operation):
    with open("operations.txt", "a") as file:
        file.write(operation + "\n")

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
        operation = f"{date} | Расход: -{expense} тенге"
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
        date, operation_data = operation.split("|", 1)
        date = date.strip()
        operation_data = operation_data.strip()

        if operation_data.startswith("Доход:"):
            amount = float(operation_data.split("+")[1].split(" тенге")[0])
            return f"{date} | Доход: +{format_money(amount)} тенге"

        elif operation_data.startswith("Расход:"):
            amount = float(operation_data.split("-")[1].split(" тенге")[0])
            return f"{date} | Расход: -{format_money(amount)} тенге"

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
        print("5. Выход")

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
            print("До свидания!")
            break

        else:
            print("Неверный выбор. Попробуйте ещё раз.")


if __name__ == "__main__":
    main()
