def format_money(amount):
    return f"{amount:,.0f}".replace(",", " ")


def parse_operation(operation):
    operation_type = None
    amount = 0
    category = ""

    if "|" in operation:
        parts = [part.strip() for part in operation.split("|")]
        operation_data = parts[1]

        if len(parts) > 2 and parts[2].startswith("Категория:"):
            category = parts[2].replace("Категория:", "").strip()
    else:
        operation_data = operation

    if operation_data.startswith("Доход:"):
        operation_type = "Доход"
        amount = float(
            operation_data.split("+")[1].split(" тенге")[0]
        )

    elif operation_data.startswith("Расход:"):
        operation_type = "Расход"
        amount = float(
            operation_data.split("-")[1].split(" тенге")[0]
        )

    return operation_type, amount, category


def get_amount(message):
    while True:
        try:
            amount = float(input(message))

            if amount <= 0:
                print("Ошибка: сумма должна быть больше 0.")
                continue

            return amount

        except ValueError:
            print("Ошибка: введите число.")
