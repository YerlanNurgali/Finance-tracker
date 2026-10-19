"""Optional check against the local PostgreSQL sandbox that every amount the API
accepts fits the NUMERIC(14, 2) columns, and every other one is a 422.

Skipped unless FT_TEST_POSTGRES_URL is set. The sandbox is one local, disposable
database with a dedicated role and the schema ft_test, marked by tests/pg_sandbox.py
(see tests/db_safety.py and the README). The test drops the application tables in
that schema and nowhere else.
"""
import pytest

import api
import database
from db_safety import sandbox_configured, sandbox_sql

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not sandbox_configured(), reason="FT_TEST_POSTGRES_URL is not set"),
]
psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(pg_database_url):
    database.close_pool()
    sandbox_sql("DROP TABLE IF EXISTS ft_test.budget_alert_state, ft_test.user_settings, ft_test.goals, "
                "ft_test.budgets, ft_test.operations, ft_test.user_categories, ft_test.users CASCADE")
    api.limiter.reset()
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        token = test_client.post("/auth/register", json={"email": "a@example.test", "password": "secret12"}).json()["access_token"]
        test_client.headers["Authorization"] = f"Bearer {token}"
        yield test_client
    database.close_pool()


@pytest.mark.parametrize("amount,status", [
    (0.001, 422), (0.005, 201), (999999999999.99, 201), ("999999999999.994", 201),
    ("999999999999.995", 422), (1e12, 422), (1e15, 422),
])
def test_operation_amount_limits(client, amount, status):
    # A string keeps the exact decimal digits; JSON numbers become floats.
    body = '{"date": "16.10.2026", "operation_type": "Доход", "amount": %s}' % amount
    response = client.post("/operations", content=body.encode(), headers={"Content-Type": "application/json"})
    assert response.status_code == status


def test_budget_and_goal_amount_limits(client):
    assert client.post("/budgets", json={"category": "Еда", "amount": 0.004}).status_code == 422
    assert client.post("/goals", json={"title": "Цель", "target_amount": 1e13}).status_code == 422
    assert client.post("/goals", json={"title": "Цель", "target_amount": 100}).status_code == 201
    goal_id = client.get("/goals").json()[0]["id"]
    assert client.put(f"/goals/{goal_id}/progress", json={"current_amount": 1e13}).status_code == 422
    assert client.put(f"/goals/{goal_id}/progress", json={"current_amount": 50.555}).status_code == 200
    assert client.get("/goals").json()[0]["current_amount"] == 50.56
