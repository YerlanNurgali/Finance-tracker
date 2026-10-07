from datetime import datetime, timedelta
from pathlib import Path
import csv
import io
import json

from fastapi import FastAPI, HTTPException, Query, Header, Depends, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator
from typing import Optional

import database
import auth

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
VALID_PERIODS = {"all", "today", "week", "month"}


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

    def render(self, content):
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")


app = FastAPI(
    title="Finance Tracker API v2",
    version="2.0.0",
    default_response_class=UTF8JSONResponse,
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# Pydantic models
class RegisterPayload(BaseModel):
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)

    @validator("email")
    def validate_email(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v:
            raise ValueError("Некорректный email адрес")
        return v


class LoginPayload(BaseModel):
    email: str
    password: str


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


class BudgetPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0)
    period: str = Field(default="month", max_length=32)


class GoalPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=150)
    target_amount: float = Field(..., gt=0)
    current_amount: float = Field(default=0, ge=0)
    deadline: Optional[str] = Field(default=None, max_length=64)


class GoalProgressPayload(BaseModel):
    current_amount: float = Field(..., ge=0)


class ProfileSettingsPayload(BaseModel):
    language: Optional[str] = Field(default=None, max_length=10)
    theme: Optional[str] = Field(default=None, max_length=10)
    currency: Optional[str] = Field(default=None, max_length=10)

    @validator("language")
    def valid_lang(cls, v):
        if v and v.upper() not in {"KZ", "EN", "RU", "KK"}:
            raise ValueError("Недопустимый язык")
        return v.upper() if v else None

    @validator("theme")
    def valid_theme(cls, v):
        if v and v.lower() not in {"light", "dark"}:
            raise ValueError("Недопустимая тема")
        return v.lower() if v else None

    @validator("currency")
    def valid_currency(cls, v):
        if v and v.upper() not in {"KZT", "USD", "EUR", "RUB"}:
            raise ValueError("Недопустимая валюта")
        return v.upper() if v else None


class PasswordChangePayload(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class DeleteAccountPayload(BaseModel):
    password: str = Field(..., min_length=1)


# Auth dependency
def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    token = authorization.split(" ")[1]
    payload = auth.verify_access_token(token)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный или просроченный токен")

    user_row = database.get_user_by_id(payload["user_id"])
    if not user_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")

    return {"id": user_row[0], "email": user_row[1]}


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


# --- Auth & Profile Routes ---
@app.post("/auth/register", status_code=201)
def register_user(payload: RegisterPayload):
    try:
        pwd_hash = auth.hash_password(payload.password)
        user_id = database.create_user(payload.email, pwd_hash)
    except database.UserAlreadyExists as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    token = auth.create_access_token({"user_id": user_id, "email": payload.email})
    return {"access_token": token, "token_type": "bearer", "email": payload.email}


@app.post("/auth/login")
def login_user(payload: LoginPayload):
    user_row = database.get_user_by_email(payload.email)
    if not user_row or not auth.verify_password(user_row[2], payload.password):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    user_id, email = user_row[0], user_row[1]
    token = auth.create_access_token({"user_id": user_id, "email": email})
    return {"access_token": token, "token_type": "bearer", "email": email}


@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    settings = database.get_user_settings(current_user["id"])
    return {**current_user, "settings": settings}


@app.put("/auth/profile")
def update_profile(payload: ProfileSettingsPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_user_settings(
            current_user["id"],
            language=payload.language,
            theme=payload.theme,
            currency=payload.currency
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Профиль обновлен"}


@app.put("/auth/password")
def change_password(payload: PasswordChangePayload, current_user: dict = Depends(get_current_user)):
    user_row = database.get_user_by_id(current_user["id"])
    if not user_row or not auth.verify_password(user_row[2], payload.current_password):
        raise HTTPException(status_code=401, detail="Неверный текущий пароль")

    new_hash = auth.hash_password(payload.new_password)
    database.update_user_password(current_user["id"], new_hash)
    return {"message": "Пароль изменен"}


@app.delete("/auth/delete")
def delete_account(payload: DeleteAccountPayload, current_user: dict = Depends(get_current_user)):
    user_row = database.get_user_by_id(current_user["id"])
    if not user_row or not auth.verify_password(user_row[2], payload.password):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    database.delete_user_account(current_user["id"])
    return {"message": "Аккаунт удален"}


# --- Date & Filtering Helpers ---
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


def _rows(user_id, period):
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail="Недопустимый период")
    return filter_operations(database.get_operations(user_id), period)


def _operation_dict(row):
    operation_id, date, operation_type, amount, category = row
    return {"id": operation_id, "date": date, "type": operation_type, "amount": float(amount), "category": category}


# --- Operations Routes ---
@app.get("/operations")
def list_operations(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    return [_operation_dict(row) for row in _rows(current_user["id"], period)]


@app.get("/balance")
def get_balance(current_user: dict = Depends(get_current_user)):
    rows = database.get_operations(current_user["id"])
    balance = sum((float(row[3]) if row[2] == "Доход" else -float(row[3])) for row in rows)
    return {"balance": balance}


@app.get("/statistics")
def get_statistics(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    rows = _rows(current_user["id"], period)
    income = sum(float(row[3]) for row in rows if row[2] == "Доход")
    expense = sum(float(row[3]) for row in rows if row[2] == "Расход")
    balance = income - expense
    savings_rate = (balance / income * 100) if income > 0 else 0.0

    categories = {}
    monthly_trend = {}
    for row in rows:
        dt = parse_operation_date(row[1])
        month_key = dt.strftime("%Y-%m") if dt else "Другое"
        if month_key not in monthly_trend:
            monthly_trend[month_key] = {"income": 0.0, "expense": 0.0}

        if row[2] == "Доход":
            monthly_trend[month_key]["income"] += float(row[3])
        else:
            monthly_trend[month_key]["expense"] += float(row[3])
            if row[4]:
                categories[row[4]] = categories.get(row[4], 0) + float(row[3])

    return {
        "total_income": income,
        "total_expense": expense,
        "balance": balance,
        "savings_rate": round(savings_rate, 1),
        "operations_count": len(rows),
        "categories": categories,
        "monthly_trend": monthly_trend
    }


@app.post("/operations", status_code=201)
def create_operation(operation: OperationPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.add_operation(current_user["id"], operation.date, operation.operation_type, operation.amount, operation.category)

        notification = None
        if operation.operation_type == "Расход" and operation.category:
            budgets = database.get_budgets(current_user["id"])
            rows = _rows(current_user["id"], "month")
            spent_map = {}
            for row in rows:
                if row[2] == "Расход" and row[4]:
                    spent_map[row[4]] = spent_map.get(row[4], 0.0) + float(row[3])

            for b in budgets:
                if b[1] == operation.category and spent_map.get(b[1], 0.0) > float(b[2]):
                    notification = f"⚠️ Бюджет на {b[1]} превышен!"
                    break

        return {"message": "Операция добавлена", "notification": notification}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/operations/{operation_id}")
def remove_operation(operation_id: int, current_user: dict = Depends(get_current_user)):
    try:
        database.delete_operation(operation_id, current_user["id"])
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"message": "Операция удалена"}


@app.put("/operations/{operation_id}")
def modify_operation(operation_id: int, operation: OperationPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_operation(
            operation_id, current_user["id"], operation.date, operation.operation_type, operation.amount, operation.category
        )
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Операция обновлена"}


# --- Budgets Routes ---
@app.get("/budgets")
def list_budgets(current_user: dict = Depends(get_current_user)):
    budgets = database.get_budgets(current_user["id"])
    # Calculate spending per category for current month
    rows = _rows(current_user["id"], "month")
    spent_map = {}
    for row in rows:
        if row[2] == "Расходов" or row[2] == "Расход":
            cat = row[4]
            if cat:
                spent_map[cat] = spent_map.get(cat, 0.0) + float(row[3])

    result = []
    for b in budgets:
        bid, category, amount, period = b
        spent = spent_map.get(category, 0.0)
        amt = float(amount)
        percent = (spent / amt * 100) if amt > 0 else 0.0
        result.append({
            "id": bid,
            "category": category,
            "amount": amt,
            "spent": spent,
            "remaining": max(0.0, amt - spent),
            "percent": round(percent, 1),
            "period": period,
            "exceeded": spent > amt
        })
    return result


@app.post("/budgets", status_code=201)
def create_budget(payload: BudgetPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.add_budget(current_user["id"], payload.category, payload.amount, payload.period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Бюджет добавлен"}


@app.put("/budgets/{budget_id}")
def edit_budget(budget_id: int, payload: BudgetPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_budget(budget_id, current_user["id"], payload.amount)
    except database.BudgetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Бюджет обновлен"}


@app.delete("/budgets/{budget_id}")
def remove_budget(budget_id: int, current_user: dict = Depends(get_current_user)):
    try:
        database.delete_budget(budget_id, current_user["id"])
    except database.BudgetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"message": "Бюджет удален"}


# --- Goals Routes ---
@app.get("/goals")
def list_goals(current_user: dict = Depends(get_current_user)):
    goals = database.get_goals(current_user["id"])
    result = []
    for g in goals:
        gid, title, target_amount, current_amount, deadline = g
        target = float(target_amount)
        current = float(current_amount)
        percent = (current / target * 100) if target > 0 else 0.0
        result.append({
            "id": gid,
            "title": title,
            "target_amount": target,
            "current_amount": current,
            "progress_percent": min(100.0, round(percent, 1)),
            "deadline": deadline
        })
    return result


@app.post("/goals", status_code=201)
def create_goal(payload: GoalPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.add_goal(current_user["id"], payload.title, payload.target_amount, payload.current_amount, payload.deadline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Цель добавлена"}


@app.put("/goals/{goal_id}/progress")
def update_goal(goal_id: int, payload: GoalProgressPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_goal_progress(goal_id, current_user["id"], payload.current_amount)
    except database.GoalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Прогресс цели обновлен"}


@app.delete("/goals/{goal_id}")
def remove_goal(goal_id: int, current_user: dict = Depends(get_current_user)):
    try:
        database.delete_goal(goal_id, current_user["id"])
    except database.GoalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"message": "Цель удалена"}


# --- Export Routes ---
@app.get("/export/csv")
def export_csv(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    rows = _rows(current_user["id"], period)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Дата", "Тип", "Сумма", "Категория"])
    for row in rows:
        writer.writerow(row)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.csv"}
    )


@app.get("/export/excel")
def export_excel(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl не установлен")

    rows = _rows(current_user["id"], period)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Операции"
    ws.append(["ID", "Дата", "Тип", "Сумма", "Категория"])
    for row in rows:
        ws.append([row[0], row[1], row[2], float(row[3]), row[4] or ""])

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    return StreamingResponse(
        file_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.xlsx"}
    )


@app.get("/export/pdf")
def export_pdf(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
    except ImportError:
        raise HTTPException(status_code=500, detail="reportlab не установлен")

    rows = _rows(current_user["id"], period)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, f"Finance Tracker - Отчет ({period})")
    p.setFont("Helvetica", 10)
    p.drawString(50, height - 70, f"Пользователь: {current_user['email']} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    y = height - 100
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Дата")
    p.drawString(150, y, "Тип")
    p.drawString(230, y, "Сумма")
    p.drawString(330, y, "Категория")
    y -= 20
    p.setFont("Helvetica", 10)

    for row in rows:
        if y < 50:
            p.showPage()
            y = height - 50
        p.drawString(50, y, str(row[1]))
        p.drawString(150, y, str(row[2]))
        p.drawString(230, y, f"{row[3]} ₸")
        p.drawString(330, y, str(row[4] or "-"))
        y -= 18

    p.save()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.pdf"}
    )
