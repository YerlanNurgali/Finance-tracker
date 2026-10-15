"""Optional check against a real, throwaway PostgreSQL that every amount the API
accepts fits the NUMERIC(14, 2) columns, and every other one is a 422.

Skipped unless FT_TEST_POSTGRES_URL points at a disposable database. Never point
it at Neon or any database with real data: the test drops the application tables.
"""
import os

import pytest

import api
import database

URL = os.getenv("FT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="FT_TEST_POSTGRES_URL is not set")
psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    database.close_pool()
    connection = psycopg2.connect(URL)
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS user_settings, goals, budgets, operations, users CASCADE")
    finally:
        connection.close()
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
