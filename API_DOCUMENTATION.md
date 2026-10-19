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
      "currency": "KZT",
      "budget_alerts_inapp": true,
      "budget_alerts_push": true
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
    "currency": "USD",
    "budget_alerts_inapp": false,
    "budget_alerts_push": true
  }
  ```
  Every field is optional; a missing one stays as it is. The two budget alert switches must be real
  booleans (`true`/`false`), anything else is `422`. Both are on for every account by default.
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
- **Response**: `200 OK` `{"message": "Пароль изменен", "access_token": "<token>", "token_type": "bearer"}`
- Смена пароля отзывает все прежние токены; текущая сессия продолжает работу с `access_token` из ответа.

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
- **`date`** (also for `PUT`): the date and time of the operation as text. It is checked on the server and
  never replaced by the current time:
  - accepted forms: `DD.MM.YYYY, HH:MM:SS` (what the frontend and the CSV import write), `DD.MM.YYYY, HH:MM`,
    `DD.MM.YYYY HH:MM`, `YYYY-MM-DDTHH:MM`, `YYYY-MM-DD`, and a bare `DD.MM.YYYY`. The text is stored as sent
    (spaces at both ends are trimmed); only a bare `DD.MM.YYYY` is stored as midnight, `DD.MM.YYYY, 00:00:00`,
    because the budgets cannot read a date without a time;
  - not earlier than `01.01.2000, 00:00:00` and not later than 31 days after the server's clock (a planned
    operation can be entered a month ahead, and the margin absorbs time zones). Operations with a future date
    count in the balance at once; there is no scheduling;
  - an unreadable or out-of-range date gives `422` with `Некорректная дата операции`,
    `Дата операции не может быть раньше 01.01.2000` or `Дата операции не может быть позже, чем через 31 день`;
    nothing is stored or changed.
  Rows saved earlier are not re-checked or rewritten and are returned with their original text. An expense dated
  in an earlier month is counted in the budget of **that** month and raises no alert (only the current month
  alerts).
- **Response**: `201 Created`
  ```json
  {
    "message": "Операция добавлена",
    "notification": null,
    "alerts": []
  }
  ```
  `alerts` lists the budget alerts this change caused (see *Budget alerts* below), `notification` is the
  Russian sentence of the most severe one, or `null`. `notification` is the field older clients read.
  If the alert check fails, the operation is still saved and the answer is the same with `alerts: []`.

### Update Operation
- **URL**: `PUT /operations/{id}`
- **Body**: (same as create)
- **Response**: `200 OK` `{"message": "Операция обновлена", "notification": null, "alerts": []}`
  The budgets of the old category and month and of the new ones are recalculated.

### Delete Operation
- **URL**: `DELETE /operations/{id}`
- **Response**: `200 OK` `{"message": "Операция удалена"}`; the budgets it counted against are recalculated without an alert.

---

## 3. Budgets & Goals

### Budgets
- `GET /budgets` — List budgets with the spending of the **current calendar month** (not the last 30 days)
  and progress. Each item has `id`, `category`, `amount`, `spent`, `remaining`, `percent`, `period`, `exceeded`
  and, added with budget alerts, `month` (`"2026-10"`) and `level` (`0`–`3`, see below).
- `POST /budgets` — Create budget (`category`, `amount`, `period`).
- `PUT /budgets/{id}` — Update budget limit (only `amount` changes; `category` and `period` of the request are ignored).
- `DELETE /budgets/{id}` — Delete budget.

### Budget alerts
A budget measures the expenses of its category in one calendar month, by the date of each operation.
The level is the share spent: `0` below 80 %, `1` warning from 80 %, `2` limit at exactly 100 %,
`3` exceeded above 100 %. The level reached is stored per budget and month, and an alert is produced only when
it **rises**: another expense at the same level, a page refresh or a repeated CSV import alerts nothing. When spending
falls (an operation is edited or deleted, a budget limit is raised) the level drops without an alert, so crossing a
threshold again alerts again. Only the current month alerts; other months just keep their level.

Operations (`POST`, `PUT`), and `POST /import/csv/commit` for the rows it really stored, return the alerts:
```json
{"alerts": [{"budget_id": 1, "category": "Еда", "month": "2026-10", "level": "warning",
             "percent": 85.0, "spent": 850.0, "amount": 1000.0}]}
```
`level` is `warning`, `limit` or `exceeded`; the client writes the text in the user's language. A jump over several
levels gives one alert for the highest. With `budget_alerts_inapp` off `alerts` is always empty, but levels are still
recorded, so switching it on later does not replay old crossings.

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
- All exports need `Authorization: Bearer <token>` and contain only the signed-in user's operations. Text that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'` in CSV and Excel; amounts stay numbers.
- If Excel or PDF export cannot run on the server (missing library or font) the answer is `503` with a generic message.

---

## 5. Categories

A user can keep their own categories next to the built-in ones. Every endpoint needs
`Authorization: Bearer <token>`; the owner is always the signed-in user (a `user_id` in a request body is
ignored), and the id of another user's category answers `404` like a missing one.

Operations and budgets store a category as plain text, so a category is only a name the user manages:
creating one does not change any data, and operations may still carry any text (an imported `Salary`, for
instance). The built-in categories cannot be renamed or deleted; they have no id.

Built-in categories — `Доход`: Зарплата, Фриланс, Бизнес, Инвестиции, Другое.
`Расход`: Еда, Транспорт, Дом, Развлечения, Здоровье, Другое. (The interface shows the same ones; their English
and Kazakh names are taken as built-in names too, see *Names* below.)

### Names
A name is trimmed (spaces at both ends) and must be 1–50 characters with no control characters (NUL, line
breaks, tabs). It must not be the name of a built-in category — in Russian, English or Kazakh, in any letter
case, for either kind — and must not repeat another category of the same user and the same kind in any letter
case (`Хобби` = `хобби`). The same name may exist once for income and once for expense. A user may have at most
**50** categories of their own (both kinds together).

### List
- **URL**: `GET /categories`
- **Response**: `200 OK`
  ```json
  {
    "limit": 50,
    "categories": [
      {"id": null, "kind": "Доход", "name": "Зарплата", "builtin": true},
      {"id": null, "kind": "Расход", "name": "Еда", "builtin": true},
      {"id": 12, "kind": "Расход", "name": "Хобби", "builtin": false}
    ]
  }
  ```
  The built-in categories come first (income, then expense), the user's own after them in the order they were
  created.

### Create
- **URL**: `POST /categories`
- **Body**: `{"kind": "Расход", "name": "Хобби"}` (`kind` is `Доход` or `Расход`)
- **Response**: `201 Created`
  ```json
  {"message": "Категория добавлена", "category": {"id": 12, "kind": "Расход", "name": "Хобби", "builtin": false}}
  ```

### Rename
- **URL**: `PUT /categories/{id}`
- **Body**: `{"name": "Увлечения"}`
- **Response**: `200 OK`
  ```json
  {
    "message": "Категория переименована",
    "category": {"id": 12, "kind": "Расход", "name": "Увлечения", "builtin": false},
    "operations_updated": 3,
    "budgets_updated": 1
  }
  ```
  In one transaction the category's name and the category text of the user's **operations of the same kind**
  change; for an expense category the user's **budgets** with that category change too. Nothing of another user
  or of the other kind is touched. A change of letter case only (`Хобби` → `ХОББИ`) is a rename; the same name
  changes nothing (both counts `0`).

### Delete
- **URL**: `DELETE /categories/{id}?move_to=<name>` (`move_to` is optional)
- **Response**: `200 OK`
  ```json
  {"message": "Категория удалена", "kind": "Расход", "name": "Хобби", "moved_to": "Другое", "operations_moved": 3}
  ```
  No operation is deleted. In one transaction the user's operations of that kind move from the category to
  `move_to` (`Другое` when it is not given or empty), then the category is deleted. `move_to` is matched without
  regard to letter case and can be a built-in category of the same kind (by any of its three names) or another
  category of the same user and kind; `moved_to` returns the name as stored. After a move of expenses the stored
  budget levels of the target category follow silently, with no alert.
  An **expense category that has a budget is not deleted** (`409`): delete or change the budget and repeat.

### Errors
| Status | `detail` | When |
|---|---|---|
| `401` | `Требуется авторизация` / invalid token | no valid token |
| `404` | `Категория не найдена` | the id does not exist, belongs to another user, or is not a valid id |
| `409` | `Категория с таким названием уже существует` | create or rename to a name the user already has for that kind |
| `409` | `На категорию назначен бюджет: удалите или измените бюджет и повторите` | delete of an expense category with a budget |
| `422` | `Название категории должно содержать от 1 до 50 символов` | empty after trimming, or longer than 50 |
| `422` | `Название категории содержит недопустимые символы` | control characters |
| `422` | `Название совпадает со встроенной категорией` | a built-in name (any of the three languages) |
| `422` | `Достигнут лимит пользовательских категорий (50)` | create with 50 categories already |
| `422` | `Категория для переноса не найдена` | `move_to` is not a built-in category or a category of the user, of that kind |
| `422` | `Нельзя перенести операции в удаляемую категорию` | `move_to` is the category being deleted |
| `422` | list of field errors | a missing or wrong `kind`/`name`, a NUL character, a name over 200 characters, a non-numeric id |

If a request fails half-way, nothing changes: renaming and deleting are single transactions.
