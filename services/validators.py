from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from services.errors import ValidationError

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GENDERS = ("male", "female", "other")


def required(value: str | None, field: str, max_len: int | None = None) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationError(f"{field} is required.", field)
    if max_len and len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.", field)
    return text


def optional(value: str | None, max_len: int | None = None, field: str = "Value") -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if max_len and len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.", field)
    return text


def phone(value: str | None, field: str = "Phone number", is_required: bool = True) -> str | None:
    raw = (value or "").strip()
    if not raw:
        if is_required:
            raise ValidationError(f"{field} is required.", field)
        return None
    digits = re.sub(r"[\s\-()]", "", raw)
    body = digits[1:] if digits.startswith("+") else digits
    if not body.isdigit() or not 7 <= len(body) <= 15:
        raise ValidationError(f"{field} must contain 7 to 15 digits.", field)
    return digits


def email(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not _EMAIL.match(text) or len(text) > 200:
        raise ValidationError("Email address is not valid.", "Email")
    return text.lower()


def gender(value: str | None, is_required: bool = False) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        if is_required:
            raise ValidationError("Gender is required.", "Gender")
        return None
    if text not in GENDERS:
        raise ValidationError(f"Gender must be one of: {', '.join(GENDERS)}.", "Gender")
    return text


def money(value: object, field: str = "Amount", allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} must be a number.", field) from None
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValidationError(
            f"{field} must be {'zero or more' if allow_zero else 'greater than zero'}.", field
        )
    if amount >= Decimal("10000000"):
        raise ValidationError(f"{field} is too large.", field)
    return amount


def positive_int(value: object, field: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValidationError(f"{field} must be a whole number.", field) from None
    if number <= 0:
        raise ValidationError(f"{field} must be greater than zero.", field)
    return number
