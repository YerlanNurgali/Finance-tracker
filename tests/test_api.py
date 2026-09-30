from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import api
import database


ROWS = [
    (1, "30.09.2026 10:00", "Доход", Decimal("10000.00"), "Зарплата"),
    (2, "30.09.2026 11:00", "Расход", Decimal("2500.00"), "Еда"),
]


def test_api_balance_operations_and_statistics(monkeypatch):
    monkeypatch.setattr(api, "get_operations", lambda: ROWS)

    assert api.get_balance() == {"balance": 7500.0}
    operations = api.list_operations("all")
    assert operations[0]["amount"] == 10000.0
    assert operations[1]["category"] == "Еда"

    statistics = api.get_statistics("all")
    assert statistics["total_income"] == 10000.0
    assert statistics["total_expense"] == 2500.0
    assert statistics["balance"] == 7500.0
    assert statistics["categories"] == {"Еда": 2500.0}


def test_api_json_response_is_utf8(monkeypatch):
    monkeypatch.setattr(api, "get_operations", lambda: ROWS)
    response = api.UTF8JSONResponse(content=api.list_operations("all"))

    assert response.headers["content-type"] == "application/json; charset=utf-8"
    body = response.body.decode("utf-8")
    assert '"type":"Доход"' in body
    assert '"category":"Зарплата"' in body
    assert '"type":"Расход"' in body
    assert '"category":"Еда"' in body


def test_api_rejects_unknown_period():
    with pytest.raises(HTTPException) as error:
        api.list_operations("year")
    assert error.value.status_code == 422


def test_api_payload_validation_and_create(monkeypatch):
    with pytest.raises(ValidationError):
        api.OperationPayload(date="date", operation_type="Расход", amount=1, category=None)
    with pytest.raises(ValidationError):
        api.OperationPayload(date="date", operation_type="Расход", amount=1, category=" ")
    with pytest.raises(ValidationError):
        api.OperationPayload(date="date", operation_type="Доход", amount=0, category="Зарплата")
    with pytest.raises(ValidationError):
        api.OperationPayload(date="date", operation_type="Неверный", amount=1)

    calls = []
    monkeypatch.setattr(api, "add_operation", lambda *args: calls.append(args))
    payload = api.OperationPayload(date="date", operation_type="Доход", amount=5, category="Зарплата")
    assert api.create_operation(payload)["message"] == "Операция добавлена"
    assert calls == [("date", "Доход", 5.0, "Зарплата")]


def test_api_delete_and_update_not_found(monkeypatch):
    def missing(*args):
        raise database.OperationNotFound("Операция не найдена")

    original_delete = api.delete_operation
    original_update = api.update_operation
    api.delete_operation = missing
    api.update_operation = missing
    try:
        with pytest.raises(HTTPException) as delete_error:
            api.remove_operation(99)
        assert delete_error.value.status_code == 404

        payload = api.OperationPayload(date="date", operation_type="Расход", amount=1, category="Еда")
        with pytest.raises(HTTPException) as update_error:
            api.edit_operation_api(99, payload)
        assert update_error.value.status_code == 404
    finally:
        api.delete_operation = original_delete
        api.update_operation = original_update
