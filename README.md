# Finance Tracker

Веб-приложение и консольный трекер доходов и расходов на FastAPI и PostgreSQL. Production-хранилище — Neon PostgreSQL; строка подключения всегда берётся из `DATABASE_URL`.

## Возможности

- доходы и расходы с категориями;
- баланс, история, фильтры по периоду и статистика по категориям;
- редактирование и удаление операций;
- CSV-экспорт;
- адаптивный PWA-интерфейс с офлайн-кэшем последнего загруженного состояния;
- локализация интерфейса на KZ / EN / RU с сохранением выбора между перезагрузками.
- современный dashboard с фильтрами истории, быстрым добавлением операций и аналитикой;
- светлая и тёмная темы с сохранением выбора и поддержкой prefers-reduced-motion.

## Структура

`database.py` содержит PostgreSQL-слой и безопасные транзакции, `storage.py` — адаптер консольного формата, `operations.py` и `finance.py` — доменную логику, `api.py` — HTTP API, а `frontend/` — PWA.

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://user:password@host/dbname?sslmode=require'
uvicorn api:app --reload
```

Откройте `http://127.0.0.1:8000`. Для консольного режима используйте `python3 main.py`.

Подключения к базе переиспользуются через пул (`psycopg2.pool.ThreadedConnectionPool`). Необязательные переменные: `DB_POOL_MIN` (по умолчанию 5 — столько простаивающих подключений пул держит открытыми между всплесками нагрузки), `DB_POOL_MAX` (10) и `DB_POOL_TIMEOUT` — сколько секунд ждать свободного подключения (10). Значения должны быть положительными, иначе приложение не запустится и сообщит, какая переменная задана неверно; если `DB_POOL_MIN` больше `DB_POOL_MAX`, используется `DB_POOL_MAX`. Каждое подключение открывается с `connect_timeout=10`.

## Тесты

```bash
python3 -m pytest -q
```

Тесты не подключаются к production-базе: PostgreSQL-слой проверяется изолированным connection double. Для запуска самого сервера нужна установленная зависимость `psycopg2-binary` и доступный `DATABASE_URL`.

## API

- `GET /operations?period=all|today|week|month`
- `POST /operations` с JSON `{date, operation_type, amount, category}`
- `PUT /operations/{id}` и `DELETE /operations/{id}`
- `GET /balance`, `GET /statistics`, `GET /export/csv`, `GET /health`

Расходы требуют категорию, суммы должны быть положительными. Ошибки валидации возвращаются с HTTP 422, неизвестная операция — с HTTP 404.

## Neon и Render

Создание таблицы выполняется при старте приложения через `CREATE TABLE IF NOT EXISTS`; существующие записи не удаляются. На Render можно использовать включённый `render.yaml`: добавьте секретный `DATABASE_URL` в Environment и не коммитьте `.env`. Push из проекта не выполняется автоматически.

Service worker имеет версию кэша `v6`, удаляет старые версии при активации и использует сеть с fallback на кэш, поэтому обновления JavaScript не застревают в старом кэше.

Frontend использует CSS variables для единого design system, поддерживает фильтрацию истории без дополнительного запроса и позволяет переключать светлую/тёмную тему без перезагрузки. Версия service worker обновляется при изменении frontend-ресурсов.

Интерфейс поддерживает казахский (`kk`), английский (`en`) и русский (`ru`). Выбранный язык сохраняется в браузере. API и база данных продолжают использовать существующие canonical Russian values (`Доход`, `Расход`, `Зарплата`, `Еда`, `Транспорт`); перевод выполняется только на presentation layer frontend.
