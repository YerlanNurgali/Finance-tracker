from database import (
    get_operations,
    add_operation,
    delete_operation,
    update_operation
)


from database import get_operations


def load_operations_from_database():
    rows = get_operations()

    operations = []
    balance = 0

    for operation_id, date, operation_type, amount, category in rows:
        if operation_type == "Доход":
            operation = f"{date} | Доход: +{amount:g} тенге"
            balance += amount

        elif operation_type == "Расход":
            operation = f"{date} | Расход: -{amount:g} тенге"

            if category:
                operation += f" | Категория: {category}"

            balance -= amount

        else:
            continue

        operations.append(operation)

    return operations, balance




def save_operation_to_database(operation):
    parts = [part.strip() for part in operation.split("|")]

    if len(parts) < 2:
        return

    date = parts[0]
    operation_data = parts[1]

    if operation_data.startswith("Доход:"):
        amount = float(
            operation_data.split("+")[1].split(" тенге")[0].replace(" ", "")
        )

        add_operation(
            date,
            "Доход",
            amount
        )

    elif operation_data.startswith("Расход:"):
        amount = float(
            operation_data.split("-")[1].split(" тенге")[0].replace(" ", "")
        )

        category = None

        if len(parts) > 2 and parts[2].startswith("Категория:"):
            category = parts[2].replace("Категория:", "").strip()

        add_operation(
            date,
            "Расход",
            amount,
            category
        )



def delete_operation_from_database(operation):
    rows = get_operations()

    for operation_id, date, operation_type, amount, category in rows:
        if operation_type == "Доход":
            db_operation = f"{date} | Доход: +{amount:g} тенге"

        elif operation_type == "Расход":
            db_operation = f"{date} | Расход: -{amount:g} тенге"

            if category:
                db_operation += f" | Категория: {category}"

        else:
            continue

        if db_operation == operation:
            delete_operation(operation_id)
            return

def update_operation_in_database(operation, new_amount, category=None):
    rows = get_operations()

    for operation_id, date, operation_type, amount, old_category in rows:
        if operation_type == "Доход":
            db_operation = f"{date} | Доход: +{amount:g} тенге"

        elif operation_type == "Расход":
            db_operation = f"{date} | Расход: -{amount:g} тенге"

            if old_category:
                db_operation += f" | Категория: {old_category}"

        else:
            continue

        if db_operation == operation:
            update_operation(
                operation_id,
                date,
                operation_type,
                new_amount,
                category if operation_type == "Расход" else None
            )
            return
