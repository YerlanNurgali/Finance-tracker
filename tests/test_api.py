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

USER = {"id": 1, "email": "test@example.com"}


def test_api_balance_operations_and_statistics(monkeypatch):
    monkeypatch.setattr(database, "get_operations", lambda user_id: ROWS)

    assert api.get_balance(USER) == {"balance": 7500.0}
    operations = api.list_operations("all", USER)
    assert operations[0]["amount"] == 10000.0
    assert operations[1]["category"] == "Еда"

    statistics = api.get_statistics("all", USER)
    assert statistics["total_income"] == 10000.0
    assert statistics["total_expense"] == 2500.0
    assert statistics["balance"] == 7500.0
    assert statistics["categories"] == {"Еда": 2500.0}


def test_api_json_response_is_utf8(monkeypatch):
    monkeypatch.setattr(database, "get_operations", lambda user_id: ROWS)
    response = api.UTF8JSONResponse(content=api.list_operations("all", USER))

    assert response.headers["content-type"] == "application/json; charset=utf-8"
    body = response.body.decode("utf-8")
    assert '"type":"Доход"' in body
    assert '"category":"Зарплата"' in body
    assert '"type":"Расход"' in body
    assert '"category":"Еда"' in body


def test_api_rejects_unknown_period():
    with pytest.raises(HTTPException) as error:
        api.list_operations("year", USER)
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
    monkeypatch.setattr(database, "add_operation", lambda *args: calls.append(args))
    monkeypatch.setattr(database, "get_budgets", lambda user_id: [])
    # An unreadable date is refused before anything is stored.
    unreadable = api.OperationPayload(date="date", operation_type="Доход", amount=5, category="Зарплата")
    with pytest.raises(HTTPException) as date_error:
        api.create_operation(unreadable, USER)
    assert date_error.value.status_code == 422
    assert calls == []

    payload = api.OperationPayload(date="30.09.2026 10:00", operation_type="Доход", amount=5, category="Зарплата")
    assert api.create_operation(payload, USER)["message"] == "Операция добавлена"
    assert calls == [(1, "30.09.2026 10:00", "Доход", 5.0, "Зарплата")]


def test_api_delete_and_update_not_found(monkeypatch):
    def missing(*args):
        raise database.OperationNotFound("Операция не найдена")

    monkeypatch.setattr(database, "delete_operation", missing)
    monkeypatch.setattr(database, "update_operation", missing)

    with pytest.raises(HTTPException) as delete_error:
        api.remove_operation(99, USER)
    assert delete_error.value.status_code == 404

    # The date is checked first: an unreadable one is a 422, not a 404.
    unreadable = api.OperationPayload(date="date", operation_type="Расход", amount=1, category="Еда")
    with pytest.raises(HTTPException) as date_error:
        api.modify_operation(99, unreadable, USER)
    assert date_error.value.status_code == 422

    payload = api.OperationPayload(date="30.09.2026 10:00", operation_type="Расход", amount=1, category="Еда")
    with pytest.raises(HTTPException) as update_error:
        api.modify_operation(99, payload, USER)
    assert update_error.value.status_code == 404


@pytest.mark.parametrize("sent,stored", [("KZ", "KZ"), ("kz", "KZ"), ("KK", "KZ"), ("kk", "KZ"), ("en", "EN"), ("RU", "RU")])
def test_profile_language_is_stored_with_one_code_for_kazakh(sent, stored):
    assert api.ProfileSettingsPayload(language=sent).language == stored


def test_profile_language_rejects_unknown_codes():
    with pytest.raises(ValidationError):
        api.ProfileSettingsPayload(language="XX")
