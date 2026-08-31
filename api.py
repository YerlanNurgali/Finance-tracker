from pathlib import Path

from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from typing import Optional
from datetime import datetime, timedelta

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

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static"
)

def parse_operation_date(date_string):
    formats = [
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y, %H:%M:%S"
    ]

    for date_format in formats:
        try:
            return datetime.strptime(date_string, date_format)
        except ValueError:
            continue

    return None

def filter_operations(rows, period):
    now = datetime.now()

    if period == "today":
        start_date = now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    elif period == "week":
        start_date = now - timedelta(days=7)

    elif period == "month":
        start_date = now - timedelta(days=30)

    else:
        return rows

    filtered = []

    for row in rows:
        operation_date = parse_operation_date(row[1])

        if operation_date and operation_date >= start_date:
            filtered.append(row)

    return filtered

class OperationCreate(BaseModel):
    date: str
    operation_type: str
    amount: float
    category: Optional[str] = None

@app.get("/")
def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")

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
def get_all_operations(period: str = "all"):
    rows = get_operations()

    rows = filter_operations(rows, period)

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
