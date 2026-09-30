from datetime import datetime, timedelta
from pathlib import Path
import csv

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator
from typing import Optional

import database
from database import get_operations, add_operation, delete_operation, update_operation

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
VALID_PERIODS = {"all", "today", "week", "month"}

app = FastAPI(title="Finance Tracker API", version="2.0.0")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


class OperationPayload(BaseModel):
    date: str = Field(..., min_length=1, max_length=64)
    operation_type: str
    amount: float = Field(..., gt=0)
    category: Optional[str] = Field(default=None, max_length=100)

    @validator("operation_type")
    def valid_type(cls, value):
        if value not in database.VALID_OPERATION_TYPES:
            raise ValueError("Недопустимый тип операции")
        return value

    @validator("category")
    def clean_category(cls, value):
        return value.strip() if value else None

    @validator("category", always=True)
    def expense_category(cls, value, values):
        if values.get("operation_type") == "Расход" and not value:
            raise ValueError("Для расхода нужна категория")
        return value


@app.on_event("startup")
def startup():
    database.create_database()


@app.get("/")
def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(FRONTEND_DIR / "service-worker.js", media_type="application/javascript")


def parse_operation_date(date_string):
    for date_format in ("%d.%m.%Y %H:%M", "%d.%m.%Y, %H:%M", "%d.%m.%Y, %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_string, date_format)
        except (ValueError, TypeError):
            continue
    return None


def filter_operations(rows, period):
    if period not in VALID_PERIODS or period == "all":
        return rows
    now = datetime.now()
    starts = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=30),
    }
    start = starts[period]
    return [row for row in rows if (parse_operation_date(row[1]) or datetime.min) >= start]


def _rows(period):
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail="Недопустимый период")
    return filter_operations(get_operations(), period)


def _operation_dict(row):
    operation_id, date, operation_type, amount, category = row
    return {"id": operation_id, "date": date, "type": operation_type, "amount": float(amount), "category": category}


@app.get("/operations")
def list_operations(period: str = Query("all")):
    return [_operation_dict(row) for row in _rows(period)]


@app.get("/balance")
def get_balance():
    balance = sum((float(row[3]) if row[2] == "Доход" else -float(row[3])) for row in get_operations())
    return {"balance": balance}


@app.get("/statistics")
def get_statistics(period: str = Query("all")):
    rows = _rows(period)
    income = sum(float(row[3]) for row in rows if row[2] == "Доход")
    expense = sum(float(row[3]) for row in rows if row[2] == "Расход")
    categories = {}
    for row in rows:
        if row[2] == "Расход" and row[4]:
            categories[row[4]] = categories.get(row[4], 0) + float(row[3])
    return {"total_income": income, "total_expense": expense, "balance": income - expense,
            "operations_count": len(rows), "categories": categories}


@app.post("/operations", status_code=201)
def create_operation(operation: OperationPayload):
    try:
        add_operation(operation.date, operation.operation_type, operation.amount, operation.category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"message": "Операция добавлена"}


@app.delete("/operations/{operation_id}")
def remove_operation(operation_id: int):
    try:
        delete_operation(operation_id)
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": "Операция удалена", "operation_id": operation_id}


@app.put("/operations/{operation_id}")
def edit_operation_api(operation_id: int, operation: OperationPayload):
    try:
        update_operation(operation_id, operation.date, operation.operation_type, operation.amount, operation.category)
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"message": "Операция изменена", "operation_id": operation_id}


@app.get("/export/csv")
def export_csv():
    rows = get_operations()
    file_path = BASE_DIR / "operations.csv"
    with file_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["Дата", "Тип", "Сумма", "Категория"])
        writer.writerows((row[1], row[2], row[3], row[4] or "") for row in rows)
    return FileResponse(file_path, media_type="text/csv", filename="operations.csv")
