from datetime import datetime, timedelta
from pathlib import Path
import csv
import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import threading

from fastapi import FastAPI, HTTPException, Query, Header, Depends, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, field_validator, validator
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from limits import parse as parse_rate_limit  # already installed as a slowapi dependency
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse

import bank_import
import budget_alerts
import database
import auth

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
VALID_PERIODS = {"all", "today", "week", "month"}

FORWARDED_ALLOW_IPS_ENV = "FORWARDED_ALLOW_IPS"
TRUSTED_PROXY_HOPS_ENV = "TRUSTED_PROXY_HOPS"
LOGIN_EMAIL_RATE = parse_rate_limit("10/minute")


def check_forwarded_allow_ips(environ) -> None:
    """On Render uvicorn must not take the client address from X-Forwarded-For. With "*" any client
    can choose the address the connection is counted under, so every per-address limit can be bypassed."""
    if (environ.get("RENDER") or "").strip().lower() != "true":
        return
    entries = [item.strip() for item in (environ.get(FORWARDED_ALLOW_IPS_ENV) or "").split(",")]
    if "*" in entries:
        raise RuntimeError("FORWARDED_ALLOW_IPS=* is refused on Render: set it to 127.0.0.1 or remove it")


def parse_trusted_proxy_hops(raw) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        hops = int(text)
    except ValueError:
        raise RuntimeError("TRUSTED_PROXY_HOPS must be a whole number")
    if not 0 <= hops <= 5:
        raise RuntimeError("TRUSTED_PROXY_HOPS must be between 0 and 5")
    return hops


check_forwarded_allow_ips(os.environ)
TRUSTED_PROXY_HOPS = parse_trusted_proxy_hops(os.environ.get(TRUSTED_PROXY_HOPS_ENV))


def _normalised_address(text: str) -> str:
    """One spelling per address: ::ffff:a.b.c.d is the IPv4 address a.b.c.d, and IPv6 is compact and lower-case."""
    address = ipaddress.ip_address(text)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def client_ip(request: Request) -> str:
    """The address the rate limits count.

    By default the connection's address is used and X-Forwarded-For is ignored. When TRUSTED_PROXY_HOPS
    is the number of proxies in front of the app, the address the nearest proxy appended is used: an
    entry of X-Forwarded-For counted from the right, which a client cannot choose. A missing or malformed
    header falls back to the connection's address. Every address is written in one form, so the same
    client cannot get two counters.
    """
    if TRUSTED_PROXY_HOPS:
        parts = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
        if len(parts) >= TRUSTED_PROXY_HOPS:
            try:
                return _normalised_address(parts[-TRUSTED_PROXY_HOPS])
            except ValueError:
                pass
    connection = get_remote_address(request)
    try:
        return _normalised_address(connection)
    except ValueError:
        return connection  # not an address (the test client's name): counted as it is


limiter = Limiter(key_func=client_ip)


def _user_rate_key(request: Request) -> str:
    # Signed-in calls are counted per account, so every user has their own counter even behind a proxy.
    # Auth itself is enforced by get_current_user; this only picks the counter.
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        payload = auth.verify_access_token(authorization.split(" ", 1)[1])
        if payload and "user_id" in payload:
            return f"user:{payload['user_id']}"
    return client_ip(request)


def _login_email_allowed(email: str) -> bool:
    """Login is also limited per account, whatever address the attempts come from."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return limiter.limiter.hit(LOGIN_EMAIL_RATE, "login-email", digest)


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

    def render(self, content):
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")


def api_docs_enabled():
    """Interactive API docs are a local development aid. Render sets RENDER=true
    on its services, so they are off there unless API_DOCS says otherwise."""
    explicit = os.getenv("API_DOCS")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("RENDER", "").strip().lower() != "true"


API_DOCS_ENABLED = api_docs_enabled()
# Only the two interactive docs pages need an inline script/style and a CDN, so
# only they go without the policy. /openapi.json is JSON, and Swagger UI's
# /docs/oauth2-redirect is used solely for OAuth2 sign-in, which this API lacks.
CSP_EXEMPT_DOCS_PATHS = {"/docs", "/redoc"} if API_DOCS_ENABLED else set()

# The frontend loads nothing from other origins and has no inline scripts or
# styles, so everything is limited to this origin.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def is_frontend_path(path):
    return path == "/" or path == "/service-worker.js" or path.startswith("/static/")


def security_headers_for(path):
    """The headers every response for ``path`` must carry."""
    headers = {
        name: value for name, value in SECURITY_HEADERS.items()
        if not (name == "Content-Security-Policy" and path in CSP_EXEMPT_DOCS_PATHS)
    }
    if not is_frontend_path(path):
        # API responses carry the user's financial data and settings; the
        # frontend files keep their own no-cache + ETag revalidation.
        headers["Cache-Control"] = "no-store"
    return headers


class SecurityHeadersMiddleware:
    """Adds the security headers to every response that passes through it.

    A plain ASGI middleware (not BaseHTTPMiddleware), so request bodies such as
    CSV uploads stream through untouched. Starlette's outermost
    ServerErrorMiddleware answers unhandled exceptions itself, outside this
    middleware: that answer gets its headers from ``internal_server_error``.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers_for(scope["path"])

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def internal_server_error(request, exc):
    """Starlette hands an ``Exception`` handler to ServerErrorMiddleware, which
    sends this response only if none has started yet and then re-raises for the
    server log. The exception text never reaches the client."""
    return PlainTextResponse("Internal Server Error", status_code=500,
                             headers=security_headers_for(request.url.path))


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _is_password_field(name):
    return isinstance(name, str) and "password" in name.lower()


def _without_passwords(value):
    if isinstance(value, dict):
        return {key: "***" if _is_password_field(key) else _without_passwords(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_passwords(item) for item in value]
    return value


def _safe_validation_error(error):
    error = dict(error)
    if "input" in error:
        if any(_is_password_field(part) for part in error.get("loc", ())):
            del error["input"]
        else:
            # A missing or misplaced field echoes the enclosing object, which may
            # hold a password next to it.
            error["input"] = _without_passwords(error["input"])
    return _json_safe(error)


async def request_validation_error(request, exc):
    """FastAPI's own 422 response, with two changes to the echoed input: passwords
    are never sent back, and NaN and Infinity (accepted by Python's JSON parser,
    but not writable as JSON) are echoed as strings instead of failing with a 500."""
    errors = [_safe_validation_error(error) for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


app = FastAPI(
    title="Finance Tracker API v2",
    version="2.0.0",
    default_response_class=UTF8JSONResponse,
    docs_url="/docs" if API_DOCS_ENABLED else None,
    redoc_url="/redoc" if API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if API_DOCS_ENABLED else None,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_exception_handler(Exception, internal_server_error)
app.add_exception_handler(RequestValidationError, request_validation_error)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# The frontend files carry no version in their URLs, so browsers must
# revalidate them (a cheap 304 via ETag) instead of guessing a freshness
# lifetime; otherwise a deploy can leave a stale index.html or script.js.
FRONTEND_CACHE_HEADERS = {"Cache-Control": "no-cache"}


class RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(FRONTEND_CACHE_HEADERS)
        return response


app.mount("/static", RevalidatedStaticFiles(directory=FRONTEND_DIR), name="static")


# Pydantic models
class TextPayload(BaseModel):
    """PostgreSQL text cannot hold NUL, so such a string is invalid input (422)
    rather than a driver error (500). Passwords are only hashed, never stored."""

    @field_validator("*", mode="before")
    @classmethod
    def reject_nul(cls, value, info):
        if isinstance(value, str) and "\x00" in value and "password" not in info.field_name:
            raise ValueError("Текст содержит недопустимый символ")
        return value


class RegisterPayload(TextPayload):
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)

    @validator("email")
    def validate_email(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v:
            raise ValueError("Некорректный email адрес")
        return v


class LoginPayload(TextPayload):
    email: str
    password: str


class OperationPayload(TextPayload):
    date: str = Field(..., min_length=1, max_length=64)
    operation_type: str
    amount: float = Field(..., gt=0, allow_inf_nan=False)
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


class BudgetPayload(TextPayload):
    category: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0, allow_inf_nan=False)
    period: str = Field(default="month", max_length=32)


class GoalPayload(TextPayload):
    title: str = Field(..., min_length=1, max_length=150)
    target_amount: float = Field(..., gt=0, allow_inf_nan=False)
    current_amount: float = Field(default=0, ge=0, allow_inf_nan=False)
    deadline: Optional[str] = Field(default=None, max_length=64)


class GoalProgressPayload(BaseModel):
    current_amount: float = Field(..., ge=0, allow_inf_nan=False)


class CategoryPayload(TextPayload):
    """A new user category. Only the kind and the name are read: the owner is always the
    signed-in user. The name is trimmed and its length checked (1-50) by the data layer;
    the limit here only keeps absurd input out."""
    kind: str
    name: str = Field(..., min_length=1, max_length=200)

    @validator("kind")
    def valid_kind(cls, value):
        if value not in database.BUILTIN_CATEGORIES:
            raise ValueError(database.CATEGORY_KIND_MESSAGE)
        return value


class CategoryRenamePayload(TextPayload):
    name: str = Field(..., min_length=1, max_length=200)


class ProfileSettingsPayload(TextPayload):
    language: Optional[str] = Field(default=None, max_length=10)
    theme: Optional[str] = Field(default=None, max_length=10)
    currency: Optional[str] = Field(default=None, max_length=10)
    # Real booleans only: "false" or 0 from a client is a mistake, not a choice.
    budget_alerts_inapp: Optional[StrictBool] = None
    budget_alerts_push: Optional[StrictBool] = None

    @validator("language")
    def valid_lang(cls, v):
        if v and v.upper() not in {"KZ", "EN", "RU", "KK"}:
            raise ValueError("Недопустимый язык")
        # KZ is the stored code for Kazakh; KK is still accepted from older clients.
        return {"KK": "KZ"}.get(v.upper(), v.upper()) if v else None

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

    # Verify token_version to detect password-change invalidation
    db_token_version = user_row[4] if len(user_row) > 4 else 0
    if payload.get("tv", 0) != db_token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен недействителен. Войдите снова")

    return {"id": user_row[0], "email": user_row[1]}


def warn_about_configuration():
    if not auth.SECRET_KEY_CONFIGURED:
        logger.warning("SECRET_KEY не задан: токены подписываются случайным ключом "
                       "и перестанут действовать после перезапуска")
    elif len(auth.SECRET_KEY) < 32:
        logger.warning("SECRET_KEY короче 32 символов: используйте длинный случайный ключ")


@app.on_event("startup")
def startup():
    warn_about_configuration()
    database.create_database()


@app.on_event("shutdown")
def shutdown():
    database.close_pool()


@app.get("/")
def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html", headers=FRONTEND_CACHE_HEADERS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(FRONTEND_DIR / "service-worker.js", media_type="application/javascript", headers=FRONTEND_CACHE_HEADERS)


# --- Auth & Profile Routes ---
@app.post("/auth/register", status_code=201)
@limiter.limit("5/minute")
def register_user(request: Request, payload: RegisterPayload):
    try:
        pwd_hash = auth.hash_password(payload.password)
        user_id = database.create_user(payload.email, pwd_hash)
    except database.UserAlreadyExists as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    token = auth.create_access_token({"user_id": user_id, "email": payload.email, "tv": 0})
    return {"access_token": token, "token_type": "bearer", "email": payload.email}


@app.post("/auth/login")
@limiter.limit("10/minute")
def login_user(request: Request, payload: LoginPayload):
    if not _login_email_allowed(payload.email):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Попробуйте через минуту.",
                            headers={"Retry-After": "60"})
    user_row = database.get_user_by_email(payload.email)
    if not user_row or not auth.verify_password(user_row[2], payload.password):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    user_id, email = user_row[0], user_row[1]
    token_version = user_row[4] if len(user_row) > 4 else 0
    token = auth.create_access_token({"user_id": user_id, "email": email, "tv": token_version})
    return {"access_token": token, "token_type": "bearer", "email": email}


@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    settings = database.get_user_settings(current_user["id"])
    return {**current_user, "settings": settings}


@app.put("/auth/profile")
def update_profile(payload: ProfileSettingsPayload, current_user: dict = Depends(get_current_user)):
    database.update_user_settings(
        current_user["id"],
        language=payload.language,
        theme=payload.theme,
        currency=payload.currency,
        budget_alerts_inapp=payload.budget_alerts_inapp,
        budget_alerts_push=payload.budget_alerts_push,
    )
    return {"message": "Профиль обновлен"}


@app.put("/auth/password")
@limiter.limit("5/minute", key_func=_user_rate_key)
def change_password(request: Request, payload: PasswordChangePayload, current_user: dict = Depends(get_current_user)):
    user_row = database.get_user_by_id(current_user["id"])
    if not user_row or not auth.verify_password(user_row[2], payload.current_password):
        raise HTTPException(status_code=401, detail="Неверный текущий пароль")

    new_hash = auth.hash_password(payload.new_password)
    database.update_user_password(current_user["id"], new_hash)
    # The change revokes every earlier token, this session's too, so it gets a
    # new one instead of being signed out on its next request.
    user_row = database.get_user_by_id(current_user["id"])
    token = auth.create_access_token({"user_id": user_row[0], "email": user_row[1], "tv": user_row[4]})
    return {"message": "Пароль изменен", "access_token": token, "token_type": "bearer"}


@app.post("/auth/logout")
def logout(current_user: dict = Depends(get_current_user)):
    # Ends every session of this account, on every device: the token_version check in
    # get_current_user rejects all tokens issued before this call.
    database.revoke_all_sessions(current_user["id"])
    return {"message": "Сессии завершены"}


@app.delete("/auth/delete")
@limiter.limit("5/minute", key_func=_user_rate_key)
def delete_account(request: Request, payload: DeleteAccountPayload, current_user: dict = Depends(get_current_user)):
    user_row = database.get_user_by_id(current_user["id"])
    if not user_row or not auth.verify_password(user_row[2], payload.password):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    database.delete_user_account(current_user["id"])
    return {"message": "Аккаунт удален"}


# --- Date & Filtering Helpers ---
def parse_operation_date(date_string):
    return budget_alerts.parse_date(date_string)


# The date of a NEW or EDITED operation. Stored rows are never rewritten or re-checked:
# they keep whatever text they have. The formats accepted here are the ones the budgets
# can already read (budget_alerts.DATE_FORMATS) plus a bare "DD.MM.YYYY" (older API
# clients send it). The frontend and the CSV import store "DD.MM.YYYY, HH:MM:SS".
OPERATION_DATE_FORMATS = budget_alerts.DATE_FORMATS + ("%d.%m.%Y",)
STORED_DATE_FORMAT = "%d.%m.%Y, %H:%M:%S"
# A date outside this range is far more likely a typo than a real operation. The upper
# limit lets a planned operation be entered a month ahead and absorbs the difference
# between the user's clock and the server's, whatever the time zone.
MIN_OPERATION_DATE = datetime(2000, 1, 1)
MAX_OPERATION_DATE_AHEAD = timedelta(days=31)
DATE_INVALID = "Некорректная дата операции"
DATE_TOO_OLD = "Дата операции не может быть раньше 01.01.2000"
DATE_TOO_FAR = "Дата операции не может быть позже, чем через 31 день"


def checked_operation_date(value, now=None):
    """The text to store as the date of a new or edited operation, or a 422 with a
    message. Nothing is guessed: an unreadable date is refused, never replaced by the
    current one. The text is stored as sent (trimmed), except that a date without a time
    becomes midnight in the stored format, because the budgets cannot read a bare
    "DD.MM.YYYY" and would leave that operation out of every month."""
    text = value.strip() if isinstance(value, str) else ""
    for date_format in OPERATION_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, date_format)
        except ValueError:
            continue
        break
    else:
        raise HTTPException(status_code=422, detail=DATE_INVALID)
    if parsed < MIN_OPERATION_DATE:
        raise HTTPException(status_code=422, detail=DATE_TOO_OLD)
    if parsed > (now or datetime.now()) + MAX_OPERATION_DATE_AHEAD:
        raise HTTPException(status_code=422, detail=DATE_TOO_FAR)
    return parsed.strftime(STORED_DATE_FORMAT) if date_format == "%d.%m.%Y" else text


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
    date = checked_operation_date(operation.date)
    try:
        database.add_operation(current_user["id"], date, operation.operation_type, operation.amount, operation.category)
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    alerts = _budget_alerts(current_user["id"], _expense_changes(operation.operation_type, operation.category, date))
    return {"message": "Операция добавлена", **_alerts_response(alerts)}


# --- Budget alerts ---
def _expense_changes(operation_type, category, date, notify=True):
    """The (category, date, notify) a saved, edited or removed operation touches:
    only expenses count against budgets."""
    if operation_type == "Расход" and category:
        return [(category, date, notify)]
    return []


def _alert_targets(changes):
    """(category, calendar month, notify) for every budget measurement a change needs.
    Only the current month alerts; other months just keep their stored level."""
    current = set(budget_alerts.current_month_keys())
    targets = {}
    for category, date, notify in changes:
        month = budget_alerts.month_key(date)
        if category and month:
            targets[(category, month)] = targets.get((category, month), False) or (notify and month in current)
    return [(category, month, notify) for (category, month), notify in targets.items()]


def _budget_alerts(user_id, changes):
    """Recalculates the budgets the changes touch and returns the alerts the user
    should see in the app. Never raises: the change itself is already saved, and an
    error here must not turn it into a failure that invites a retry (and a duplicate).
    Only the type of an error is logged."""
    try:
        targets = _alert_targets(changes)
        if not targets:
            return []
        alerts = database.evaluate_budget_alerts(user_id, targets)
        if alerts and not database.get_user_settings(user_id).get("budget_alerts_inapp", True):
            return []
        return alerts
    except Exception as exc:
        logger.error("Budget check failed for user %s: %s", user_id, type(exc).__name__)
        return []


def _alerts_response(alerts):
    """``alerts`` is the structured list; ``notification`` stays what it always was,
    one Russian sentence or null, for clients that only know that field."""
    try:
        top = budget_alerts.most_severe(alerts)
        return {"notification": budget_alerts.legacy_text(top) if top else None, "alerts": alerts}
    except Exception as exc:  # the change is saved already; see _budget_alerts
        logger.error("Budget check failed: %s", type(exc).__name__)
        return {"notification": None, "alerts": []}


def _sync_budget_levels(user_id, category):
    """After a budget is created or changed its level follows the spending without an
    alert: the user just set that limit, so what was already spent is not news."""
    try:
        database.evaluate_budget_alerts(user_id, [(category, month, False) for month in budget_alerts.current_month_keys()])
    except Exception as exc:
        logger.error("Budget check failed for user %s: %s", user_id, type(exc).__name__)


@app.delete("/operations/{operation_id}")
def remove_operation(operation_id: int, current_user: dict = Depends(get_current_user)):
    try:
        previous = database.delete_operation(operation_id, current_user["id"])
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if previous:
        # Less spending can only lower a level, so this only keeps the stored one in step.
        operation_type, category, date = previous
        _budget_alerts(current_user["id"], _expense_changes(operation_type, category, date, notify=False))
    return {"message": "Операция удалена"}


@app.put("/operations/{operation_id}")
def modify_operation(operation_id: int, operation: OperationPayload, current_user: dict = Depends(get_current_user)):
    date = checked_operation_date(operation.date)
    try:
        previous = database.update_operation(
            operation_id, current_user["id"], date, operation.operation_type, operation.amount, operation.category
        )
    except database.OperationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The old category and month lose the amount (silently), the new ones gain it.
    changes = _expense_changes(operation.operation_type, operation.category, date)
    if previous:
        operation_type, category, previous_date = previous
        changes += _expense_changes(operation_type, category, previous_date, notify=False)
    alerts = _budget_alerts(current_user["id"], changes)
    return {"message": "Операция обновлена", **_alerts_response(alerts)}


# --- User categories ---
# Built-in categories (database.BUILTIN_CATEGORIES) cannot be changed; the user's own are
# stored in user_categories. Operations and budgets keep a category as plain text, so a
# rename or a delete updates those texts for the signed-in user only. Every query is
# restricted to the user in the token; another user's id is simply "not found".
@app.get("/categories")
def list_categories(current_user: dict = Depends(get_current_user)):
    return {"limit": database.MAX_USER_CATEGORIES, "categories": database.list_categories(current_user["id"])}


@app.post("/categories", status_code=201)
def create_category(payload: CategoryPayload, current_user: dict = Depends(get_current_user)):
    try:
        category = database.add_user_category(current_user["id"], payload.kind, payload.name)
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except database.CategoryExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Категория добавлена", "category": category}


@app.put("/categories/{category_id}")
def rename_category(category_id: int, payload: CategoryRenamePayload, current_user: dict = Depends(get_current_user)):
    try:
        result = database.rename_user_category(category_id, current_user["id"], payload.name)
    except database.CategoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except database.CategoryExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Категория переименована", **result}


@app.delete("/categories/{category_id}")
def delete_category(category_id: int, move_to: Optional[str] = Query(None, max_length=100),
                    current_user: dict = Depends(get_current_user)):
    try:
        result = database.delete_user_category(category_id, current_user["id"], move_to)
    except database.CategoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except database.CategoryInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result["kind"] == "Расход" and result["operations_moved"]:
        # More spending in the target category: its stored budget level follows, silently.
        _sync_budget_levels(current_user["id"], result["moved_to"])
    return {"message": "Категория удалена", **result}


# --- Bank CSV import ---
IMPORT_PREVIEW_LIMIT = 100
_IMPORT_ERROR_STATUS = {"file_too_large": 413, "invalid_settings": 422}


def import_settings(
    delimiter: str = Query("auto", max_length=4),
    has_header: bool = Query(True),
    date_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    date_format: str = Query("DD.MM.YYYY", max_length=16),
    amount_mode: str = Query("signed", max_length=16),
    amount_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    invert_sign: bool = Query(False),
    type_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    income_values: str = Query("", max_length=200),
    expense_values: str = Query("", max_length=200),
    income_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    expense_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    description_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    category_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    currency_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    reference_column: Optional[int] = Query(None, ge=0, lt=bank_import.MAX_COLUMNS),
    decimal_separator: str = Query("auto", max_length=4),
    default_income_category: str = Query(bank_import.DEFAULT_CATEGORY, max_length=bank_import.MAX_CATEGORY_CHARS),
    default_expense_category: str = Query(bank_import.DEFAULT_CATEGORY, max_length=bank_import.MAX_CATEGORY_CHARS),
) -> bank_import.ImportSettings:
    return bank_import.ImportSettings(
        delimiter=delimiter, has_header=has_header, date_column=date_column, date_format=date_format,
        amount_mode=amount_mode, amount_column=amount_column, invert_sign=invert_sign,
        type_column=type_column, income_values=income_values, expense_values=expense_values,
        income_column=income_column, expense_column=expense_column,
        description_column=description_column, category_column=category_column,
        currency_column=currency_column, reference_column=reference_column, decimal_separator=decimal_separator,
        default_income_category=default_income_category, default_expense_category=default_expense_category,
    )


async def _read_csv_body(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "text/csv":
        raise HTTPException(status_code=415, detail="Ожидается файл CSV")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > bank_import.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > bank_import.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой")
    return bytes(body)


async def _parse_upload(request, settings, user_id):
    raw = await _read_csv_body(request)
    user_currency = (await run_in_threadpool(database.get_user_settings, user_id))["currency"]
    try:
        return bank_import.parse_statement(raw, settings, user_currency), user_currency
    except bank_import.ImportFileError as exc:
        raise HTTPException(status_code=_IMPORT_ERROR_STATUS.get(exc.code, 400), detail=str(exc)) from exc


def _import_summary(result):
    return {
        "columns": result.columns,
        "delimiter": result.delimiter,
        "total_rows": result.total_rows,
        "empty_rows": result.empty_rows,
        "error_rows": len(result.errors),
        "errors": result.errors[:IMPORT_PREVIEW_LIMIT],
    }


@app.post("/import/csv/preview")
@limiter.limit("30/minute", key_func=_user_rate_key)
async def preview_csv_import(
    request: Request,
    settings: bank_import.ImportSettings = Depends(import_settings),
    current_user: dict = Depends(get_current_user),
):
    result, currency = await _parse_upload(request, settings, current_user["id"])
    existing = await run_in_threadpool(
        database.find_imported_hashes, current_user["id"], [row.import_hash for row in result.rows]
    )
    return {
        **_import_summary(result),
        "mapped": settings.is_mapped(),
        "suggested": bank_import.suggest_mapping(result.columns) if settings.has_header else {},
        "sample": result.sample,
        "currency": currency,
        "valid_rows": len(result.rows),
        "duplicate_rows": sum(1 for row in result.rows if row.import_hash in existing),
        "rows": [
            {
                "line": row.line,
                "date": row.date,
                "type": row.operation_type,
                "amount": float(row.amount),
                "category": row.category,
                "description": row.description[:200],
                "duplicate": row.import_hash in existing,
            }
            for row in result.rows[:IMPORT_PREVIEW_LIMIT]
        ],
    }


@app.post("/import/csv/commit")
@limiter.limit("10/minute", key_func=_user_rate_key)
async def commit_csv_import(
    request: Request,
    settings: bank_import.ImportSettings = Depends(import_settings),
    current_user: dict = Depends(get_current_user),
):
    if not settings.is_mapped():
        raise HTTPException(status_code=422, detail="Настройте столбцы для импорта")
    result, _ = await _parse_upload(request, settings, current_user["id"])
    operations = [
        (row.date, row.operation_type, row.amount, row.category, row.import_hash) for row in result.rows
    ]
    try:
        inserted = await run_in_threadpool(database.import_operations, current_user["id"], operations)
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # The exception text may quote statement values, so only its type is logged.
        logger.error("CSV import failed for user %s: %s", current_user["id"], type(exc).__name__)
        raise HTTPException(status_code=503, detail="Не удалось сохранить импорт. Данные не изменены") from None
    # Only rows that were really stored count; a repeated import inserts nothing.
    changes = [
        change for row in result.rows if row.import_hash in inserted
        for change in _expense_changes(row.operation_type, row.category, row.date)
    ]
    alerts = await run_in_threadpool(_budget_alerts, current_user["id"], changes)
    return {
        **_import_summary(result),
        "imported": len(inserted),
        "duplicate_rows": len(result.rows) - len(inserted),
        **_alerts_response(alerts),
    }


# --- Budgets Routes ---
@app.get("/budgets")
def list_budgets(current_user: dict = Depends(get_current_user)):
    budgets = database.get_budgets(current_user["id"])
    # Spending per category in the current calendar month, the same measure the alerts use.
    month = budget_alerts.current_month_key()
    spent_map = {}
    for row in database.get_operations(current_user["id"]):
        if row[2] == "Расход" and row[4] and budget_alerts.month_key(row[1]) == month:
            spent_map[row[4]] = spent_map.get(row[4], 0) + budget_alerts.to_decimal(row[3])

    result = []
    for b in budgets:
        bid, category, amount, period = b
        spent = spent_map.get(category, budget_alerts.to_decimal(0))
        amt = budget_alerts.to_decimal(amount)
        percent = (spent / amt * 100) if amt > 0 else 0
        result.append({
            "id": bid,
            "category": category,
            "amount": float(amt),
            "spent": float(spent),
            "remaining": float(max(budget_alerts.to_decimal(0), amt - spent)),
            "percent": round(float(percent), 1),
            "period": period,
            "month": month,
            "level": budget_alerts.level_for(spent, amt),
            "exceeded": spent > amt
        })
    return result


@app.post("/budgets", status_code=201)
def create_budget(payload: BudgetPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.add_budget(current_user["id"], payload.category, payload.amount, payload.period)
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _sync_budget_levels(current_user["id"], payload.category.strip())
    return {"message": "Бюджет добавлен"}


@app.put("/budgets/{budget_id}")
def edit_budget(budget_id: int, payload: BudgetPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_budget(budget_id, current_user["id"], payload.amount)
    except database.BudgetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The category is the stored budget's, not the one in the request body: only the amount changes.
    try:
        category = next((b[1] for b in database.get_budgets(current_user["id"]) if b[0] == budget_id), None)
    except Exception as exc:
        category = None
        logger.error("Budget check failed for user %s: %s", current_user["id"], type(exc).__name__)
    if category:
        _sync_budget_levels(current_user["id"], category)
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
    except database.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"message": "Цель добавлена"}


@app.put("/goals/{goal_id}/progress")
def update_goal(goal_id: int, payload: GoalProgressPayload, current_user: dict = Depends(get_current_user)):
    try:
        database.update_goal_progress(goal_id, current_user["id"], payload.current_amount)
    except database.GoalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except database.InvalidInput as exc:
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
# A spreadsheet runs a cell that starts with one of these as a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _spreadsheet_text(value):
    """User text is exported as text: the leading quote keeps a category such as
    =HYPERLINK(...) from running as a formula when the file is opened."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _export_row(row):
    operation_id, date, operation_type, amount, category = row
    return [operation_id, _spreadsheet_text(date), operation_type, amount, _spreadsheet_text(category)]


@app.get("/export/csv")
def export_csv(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    rows = _rows(current_user["id"], period)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Дата", "Тип", "Сумма", "Категория"])
    for row in rows:
        writer.writerow(_export_row(row))
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.csv"}
    )


# Characters a workbook cannot store (ASCII control codes other than tab and
# line breaks); openpyxl refuses the whole file if a cell holds one.
EXCEL_ILLEGAL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _excel_text(value):
    return EXCEL_ILLEGAL_CHARACTERS.sub("?", value) if isinstance(value, str) else value


@app.get("/export/excel")
def export_excel(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    try:
        import openpyxl
    except ImportError:
        logger.error("Excel export unavailable: openpyxl is not installed")
        raise HTTPException(status_code=503, detail="Экспорт в Excel временно недоступен")

    rows = _rows(current_user["id"], period)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Операции"
    ws.append(["ID", "Дата", "Тип", "Сумма", "Категория"])
    for row in rows:
        operation_id, date, operation_type, amount, category = _export_row(row)
        ws.append([operation_id, _excel_text(date), operation_type, float(amount), _excel_text(category) or ""])
        ws.cell(row=ws.max_row, column=4).number_format = "0.00"

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    return StreamingResponse(
        file_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.xlsx"}
    )


# --- PDF export ---
# The standard PDF fonts have no Cyrillic, so a TrueType font is embedded.
# PDF_FONT_PATH (and optionally PDF_BOLD_FONT_PATH) choose it; otherwise the
# first of these that exists and has every letter below is used.
PDF_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
]
PDF_REQUIRED_LETTERS = "АаБбЯяЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
PDF_FONT, PDF_BOLD_FONT = "FinanceTrackerSans", "FinanceTrackerSans-Bold"
_pdf_fonts = None
_pdf_fonts_lock = threading.Lock()


def _register_pdf_font(name, path):
    """Registers the font under ``name`` and returns the code points it has, or
    None if it is unreadable or lacks a Russian or Kazakh letter."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if not path or not os.path.isfile(path):
        return None
    try:
        font = TTFont(name, path)
    except Exception:
        return None
    glyphs = frozenset(font.face.charToGlyph)
    if any(ord(letter) not in glyphs for letter in PDF_REQUIRED_LETTERS):
        return None
    pdfmetrics.registerFont(font)
    return glyphs


def pdf_fonts():
    """(regular font, bold font, code points both can draw), or None when no
    suitable font is installed. Looked up once per process."""
    global _pdf_fonts
    with _pdf_fonts_lock:
        if _pdf_fonts is None:
            candidates = [(os.getenv("PDF_FONT_PATH"), os.getenv("PDF_BOLD_FONT_PATH"))] + PDF_FONT_CANDIDATES
            _pdf_fonts = False
            for regular_path, bold_path in candidates:
                glyphs = _register_pdf_font(PDF_FONT, regular_path)
                if glyphs is None:
                    continue
                bold_glyphs = _register_pdf_font(PDF_BOLD_FONT, bold_path)
                # Without a matching bold face the headings use the regular one.
                bold = PDF_BOLD_FONT if bold_glyphs is not None else PDF_FONT
                _pdf_fonts = (PDF_FONT, bold, glyphs & bold_glyphs if bold_glyphs is not None else glyphs)
                break
        return _pdf_fonts or None


def _pdf_text(value, glyphs):
    """A character the font cannot draw becomes "?" rather than an invisible box."""
    return "".join(ch if ord(ch) in glyphs else "?" for ch in str(value))


def _pdf_wrap(text, font, size, width):
    """Splits ``text`` into lines no wider than ``width``; a word longer than a
    line is broken, so nothing is cut off or runs past the column."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if stringWidth(candidate, font, size) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        while stringWidth(word, font, size) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and stringWidth(word[:cut], font, size) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    lines.append(line)
    return lines


# (heading, left edge, width, right-aligned) on a 612 pt wide Letter page.
PDF_COLUMNS = [("Дата", 50, 105, False), ("Тип", 160, 60, False), ("Сумма", 225, 110, True), ("Категория", 345, 217, False)]
PDF_MARGIN, PDF_FONT_SIZE, PDF_LEADING, PDF_ROW_GAP = 50, 10, 12, 6


@app.get("/export/pdf")
def export_pdf(period: str = Query("all"), current_user: dict = Depends(get_current_user)):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        logger.error("PDF export unavailable: reportlab is not installed")
        raise HTTPException(status_code=503, detail="Экспорт в PDF временно недоступен")
    fonts = pdf_fonts()
    if fonts is None:
        logger.error("PDF export unavailable: no font with Russian and Kazakh letters; set PDF_FONT_PATH")
        raise HTTPException(status_code=503, detail="Экспорт в PDF временно недоступен")
    font, bold, glyphs = fonts
    currency = "₸" if ord("₸") in glyphs else "KZT"

    rows = _rows(current_user["id"], period)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter, initialFontName=font)
    width, height = letter
    text_width = width - 2 * PDF_MARGIN

    def table_header(y):
        p.setFont(bold, PDF_FONT_SIZE)
        for heading, x, column_width, right in PDF_COLUMNS:
            if right:
                p.drawRightString(x + column_width, y, heading)
            else:
                p.drawString(x, y, heading)
        p.line(PDF_MARGIN, y - 4, width - PDF_MARGIN, y - 4)
        p.setFont(font, PDF_FONT_SIZE)
        return y - PDF_LEADING - PDF_ROW_GAP

    y = height - PDF_MARGIN
    p.setFont(bold, 16)
    for line in _pdf_wrap(_pdf_text(f"Finance Tracker - Отчет ({period})", glyphs), bold, 16, text_width):
        p.drawString(PDF_MARGIN, y, line)
        y -= 20
    p.setFont(font, PDF_FONT_SIZE)
    details = f"Пользователь: {current_user['email']} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    for line in _pdf_wrap(_pdf_text(details, glyphs), font, PDF_FONT_SIZE, text_width):
        p.drawString(PDF_MARGIN, y, line)
        y -= PDF_LEADING
    y = table_header(y - PDF_LEADING)

    if not rows:
        p.drawString(PDF_MARGIN, y, "Нет операций за выбранный период")
    for row in rows:
        cells = [row[1], row[2], f"{row[3]} {currency}", row[4] or "-"]
        lines = [
            _pdf_wrap(_pdf_text(value, glyphs), font, PDF_FONT_SIZE, column_width)
            for value, (_, _, column_width, _) in zip(cells, PDF_COLUMNS)
        ]
        row_height = max(len(cell) for cell in lines) * PDF_LEADING
        if y - row_height + PDF_LEADING < PDF_MARGIN:
            # A row never splits across pages, and every page repeats the header.
            p.showPage()
            y = table_header(height - PDF_MARGIN)
        for cell, (_, x, column_width, right) in zip(lines, PDF_COLUMNS):
            for index, line in enumerate(cell):
                line_y = y - index * PDF_LEADING
                if right:
                    p.drawRightString(x + column_width, line_y, line)
                else:
                    p.drawString(x, line_y, line)
        y -= row_height + PDF_ROW_GAP

    p.save()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=finance_export_{period}.pdf"}
    )
