from pydantic import BaseModel
from fastapi import FastAPI
from typing import Optional

from database import (
    get_operations,
    add_operation,
    delete_operation,
    update_operation
)
from finance import get_category_expenses

app = FastAPI(
    title="Finance Tracker API",
    description="API для управления личными финансами",
    version="1.0.0",
)

class OperationCreate(BaseModel):
    date: str
    operation_type: str
    amount: float
    category: Optional[str] = None

@app.get("/statistics")
def get_statistics():
    rows = get_operations()

    total_income = 0
    total_expense = 0

    for operation_id, date, operation_type, amount, category in rows:
        if operation_type == "Доход":
            total_income += amount

        elif operation_type == "Расход":
            total_expense += amount

    balance = total_income - total_expense

    operations = []

    for operation_id, date, operation_type, amount, category in rows:
        if operation_type == "Доход":
            operations.append(
                f"{date} | Доход: +{amount:g} тенге"
            )

        elif operation_type == "Расход":
            operation = f"{date} | Расход: -{amount:g} тенге"

            if category:
                operation += f" | Категория: {category}"

            operations.append(operation)

    categories = get_category_expenses(operations)

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
        "operations_count": len(rows),
        "categories": categories
    }

@app.get("/operations")
def get_all_operations():
    rows = get_operations()

    operations = []

    for operation_id, date, operation_type, amount, category in rows:
        operations.append({
            "id": operation_id,
            "date": date,
            "type": operation_type,
            "amount": amount,
            "category": category,
        })

    return operations

@app.get("/balance")
def get_balance():
    rows = get_operations()

    balance = 0

    for operation_id, date, operation_type, amount, category in rows:
        if operation_type == "Доход":
            balance += amount

        elif operation_type == "Расход":
            balance -= amount

    return {
        "balance": balance
    }

@app.post("/operations")
def create_operation(operation: OperationCreate):
    add_operation(
        operation.date,
        operation.operation_type,
        operation.amount,
        operation.category
    )

    return {
        "message": "Операция добавлена"
    }

@app.delete("/operations/{operation_id}")
def remove_operation(operation_id: int):
    delete_operation(operation_id)

    return {
        "message": "Операция удалена",
        "operation_id": operation_id
    }

@app.put("/operations/{operation_id}")
def edit_operation_api(
    operation_id: int,
    operation: OperationCreate
):
    update_operation(
        operation_id,
        operation.date,
        operation.operation_type,
        operation.amount,
        operation.category
    )

    return {
        "message": "Операция изменена",
        "operation_id": operation_id
    }
