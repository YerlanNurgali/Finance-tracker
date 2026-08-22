def load_operations():
    operations = []
    balance = 0

    try:
        with open("operations.txt", "r") as file:
            for line in file:
                operation = line.strip()

                if operation:
                    operations.append(operation)

                    if operation.startswith("Доход:"):
                        amount = float(operation.split("+")[1].split(" тенге")[0])
                        balance += amount

                    elif operation.startswith("Расход:"):
                        amount = float(operation.split("-")[1].split(" тенге")[0])
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

        operation = f"Доход: +{income} тенге"
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

        operation = f"Расход: -{expense} тенге"
        operations.append(operation)
        save_operation(operation)

        print("Расход добавлен!")

        return balance

    except ValueError:
        print("Ошибка: введите число.")
        return balance

def show_balance(balance):
    print(f"Ваш баланс: {balance} тенге")

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
        print(f"Ваш баланс: {balance} тенге")

    elif choice == "4":
         print("\n--- ИСТОРИЯ ОПЕРАЦИЙ ---")

         if len(operations) == 0:
             print("Операций пока нет.")
         else:
             for number, operation in enumerate(operations, start=1):
                 print(f"{number}. {operation}")

    elif choice == "5":
         print("До свидания!")
         break

    else:
        print("Неверный выбор. Попробуйте ещё раз.")
