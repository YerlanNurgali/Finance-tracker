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


def save_operation(operation):
    with open("operations.txt", "a") as file:
        file.write(operation + "\n")
