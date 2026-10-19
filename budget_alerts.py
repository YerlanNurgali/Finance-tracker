"""Budget alert rules: which calendar month an operation belongs to and which
alert level a budget has reached. Pure functions, no database, no clock of their own.

Levels, by the share of the budget that is spent:
    0  below 80 %
    1  warning: 80 % or more
    2  limit: exactly 100 % (to the cent)
    3  exceeded: more than 100 %
"""
from datetime import datetime, timedelta
from decimal import Decimal

# Every format an operation date is stored in; the frontend and the CSV import
# write the third one, API clients may send the others.
DATE_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%Y, %H:%M", "%d.%m.%Y, %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")

NO_ALERT, WARNING, LIMIT, EXCEEDED = 0, 1, 2, 3
LEVEL_NAMES = {WARNING: "warning", LIMIT: "limit", EXCEEDED: "exceeded"}
WARNING_RATIO = Decimal("0.8")


def parse_date(value):
    """The date of an operation, or None when the text is not in a known format."""
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except (ValueError, TypeError):
            continue
    return None


def month_key(value):
    """'2026-10' for the calendar month an operation date falls in, else None."""
    parsed = parse_date(value)
    return f"{parsed.year:04d}-{parsed.month:02d}" if parsed else None


def current_month_key(now=None):
    """The calendar month budgets are shown for, by the server's clock."""
    now = now or datetime.now()
    return f"{now.year:04d}-{now.month:02d}"


def current_month_keys(now=None):
    """The months that count as the current one. Operation dates carry the user's
    local time and ``now`` is the server's, so around midnight at a month boundary
    the two can disagree by a day: both months are accepted then."""
    now = now or datetime.now()
    keys = []
    for moment in (now - timedelta(days=1), now, now + timedelta(days=1)):
        key = f"{moment.year:04d}-{moment.month:02d}"
        if key not in keys:
            keys.append(key)
    return keys


def to_decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def level_for(spent, amount):
    """The alert level of ``spent`` against a budget of ``amount``."""
    spent, amount = to_decimal(spent), to_decimal(amount)
    if amount <= 0:
        return NO_ALERT
    if spent > amount:
        return EXCEEDED
    if spent == amount:
        return LIMIT
    if spent >= amount * WARNING_RATIO:
        return WARNING
    return NO_ALERT


def month_spent(rows, month):
    """Sum of the amounts of (date, amount) rows that fall in ``month``."""
    total = Decimal(0)
    for date, amount in rows:
        if month_key(date) == month:
            total += to_decimal(amount)
    return total


def make_alert(budget_id, category, month, level, spent, amount):
    spent, amount = to_decimal(spent), to_decimal(amount)
    return {
        "budget_id": budget_id,
        "category": category,
        "month": month,
        "level": LEVEL_NAMES[level],
        "percent": round(float(spent / amount * 100), 1),
        "spent": float(spent),
        "amount": float(amount),
    }


_SEVERITY = {name: level for level, name in LEVEL_NAMES.items()}


def most_severe(alerts):
    return max(alerts, key=lambda alert: _SEVERITY[alert["level"]], default=None)


def legacy_text(alert):
    """The Russian sentence of the older ``notification`` field of POST /operations;
    the text for the exceeded level is the one that field has always carried."""
    category = alert["category"]
    if alert["level"] == "exceeded":
        return f"⚠️ Бюджет на {category} превышен!"
    if alert["level"] == "limit":
        return f"⚠️ Бюджет на {category} исчерпан"
    return f"⚠️ Бюджет на {category}: израсходовано {alert['percent']:g}%"
