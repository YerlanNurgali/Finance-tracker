import re


def format_money(amount):
    return f"{float(amount):,.0f}".replace(",", " ")


def parse_amount(value):
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        normalized = str(value).strip().replace(" ", "").replace("\u00a0", "")
        amount = float(normalized.replace(",", "."))
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return amount


def parse_operation(operation):
    parts = [part.strip() for part in str(operation).split("|")]
    operation_data = parts[1] if len(parts) > 1 else parts[0]
    category = ""
    for part in parts[2:]:
        if part.startswith("Категория:"):
            category = part.split(":", 1)[1].strip()
            break
    match = re.match(r"^(Доход|Расход):\s*([+-]?[\d\s\u00a0]+(?:[.,]\d+)?)\s*тенге", operation_data)
    if not match:
        return None, 0, category
    amount = abs(float(match.group(2).replace(" ", "").replace("\u00a0", "").replace(",", ".")))
    return match.group(1), amount, category


def get_amount(message):
    while True:
        try:
            return parse_amount(input(message))
        except (TypeError, ValueError):
            print("Ошибка: введите положительную сумму.")
