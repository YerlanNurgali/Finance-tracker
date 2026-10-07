---
name: api-documentation
description: Finance Tracker API v2 Documentation (Authentication, Users, Operations, Budgets, Goals, Settings, Exports)
type: reference
---

# Finance Tracker API v2 — Documentation

Base URL: `http://localhost:8000` (or your production domain on Render)

## 1. Authentication & Users

### Register
- **URL**: `POST /auth/register`
- **Body**:
  ```json
  {
    "email": "user@example.com",
    "password": "securepassword123"
  }
  ```
- **Response**: `201 Created`
  ```json
  {
    "access_token": "eyJ...",
    "token_type": "bearer",
    "email": "user@example.com"
  }
  ```

### Login
- **URL**: `POST /auth/login`
- **Body**:
  ```json
  {
    "email": "user@example.com",
    "password": "securepassword123"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "access_token": "eyJ...",
    "token_type": "bearer",
    "email": "user@example.com"
  }
  ```

### Get Current User Profile & Settings
- **URL**: `GET /auth/me`
- **Headers**: `Authorization: Bearer <token>`
- **Response**: `200 OK`
  ```json
  {
    "id": 1,
    "email": "user@example.com",
    "settings": {
      "language": "RU",
      "theme": "light",
      "currency": "KZT"
    }
  }
  ```

### Update Profile Settings
- **URL**: `PUT /auth/profile`
- **Headers**: `Authorization: Bearer <token>`
- **Body**:
  ```json
  {
    "language": "EN",
    "theme": "dark",
    "currency": "USD"
  }
  ```
- **Response**: `200 OK` `{"message": "Профиль обновлен"}`

### Change Password
- **URL**: `PUT /auth/password`
- **Headers**: `Authorization: Bearer <token>`
- **Body**:
  ```json
  {
    "current_password": "securepassword123",
    "new_password": "newsecurepassword456"
  }
  ```
- **Response**: `200 OK` `{"message": "Пароль изменен"}`

### Delete Account
- **URL**: `DELETE /auth/delete`
- **Headers**: `Authorization: Bearer <token>`
- **Body**:
  ```json
  {
    "password": "newsecurepassword456"
  }
  ```
- **Response**: `200 OK` `{"message": "Аккаунт удален"}` (Cascades deletion of operations, budgets, goals, settings).

---

## 2. Operations & Balance

All endpoints require `Authorization: Bearer <token>` and filter data strictly by the authenticated user ID.

### List Operations
- **URL**: `GET /operations?period=all` (periods: `all`, `today`, `week`, `month`)
- **Response**: `200 OK`
  ```json
  [
    {
      "id": 1,
      "date": "07.10.2026 12:00",
      "type": "Доход",
      "amount": 150000.0,
      "category": "Зарплата"
    }
  ]
  ```

### Get Balance
- **URL**: `GET /balance`
- **Response**: `200 OK` `{"balance": 150000.0}`

### Get Statistics
- **URL**: `GET /statistics?period=month`
- **Response**: `200 OK`
  ```json
  {
    "total_income": 150000.0,
    "total_expense": 25000.0,
    "balance": 125000.0,
    "savings_rate": 83.3,
    "operations_count": 2,
    "categories": {"Еда": 25000.0},
    "monthly_trend": {"2026-10": {"income": 150000.0, "expense": 25000.0}}
  }
  ```

### Create Operation
- **URL**: `POST /operations`
- **Body**:
  ```json
  {
    "date": "07.10.2026 14:00",
    "operation_type": "Расход",
    "amount": 5000.0,
    "category": "Еда"
  }
  ```
- **Response**: `201 Created` `{"message": "Операция добавлена", "notification": null}`

### Update Operation
- **URL**: `PUT /operations/{id}`
- **Body**: (same as create)

### Delete Operation
- **URL**: `DELETE /operations/{id}`

---

## 3. Budgets & Goals

### Budgets
- `GET /budgets` — List monthly budgets with current spending & progress.
- `POST /budgets` — Create budget (`category`, `amount`, `period`).
- `PUT /budgets/{id}` — Update budget limit.
- `DELETE /budgets/{id}` — Delete budget.

### Goals
- `GET /goals` — List financial goals.
- `POST /goals` — Create goal (`title`, `target_amount`, `current_amount`, `deadline`).
- `PUT /goals/{id}/progress` — Update saved amount.
- `DELETE /goals/{id}` — Delete goal.

---

## 4. Export

- `GET /export/csv?period=all` — Download CSV report.
- `GET /export/excel?period=all` — Download Excel report (`openpyxl`).
- `GET /export/pdf?period=all` — Download PDF report (`reportlab`).
