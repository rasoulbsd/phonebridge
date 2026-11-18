import re

_NON_DIGITS = re.compile(r"\D")
# Matching on the last 10 digits lets "+98 912 123 4567" and "09121234567" land in
# the same conversation without knowing the country.
_MATCH_DIGITS = 10
_MIN_PHONE_DIGITS = 7


def normalize_address(raw: str) -> str:
    value = raw.strip()
    if "@" in value:
        return value.lower()
    digits = _NON_DIGITS.sub("", value)
    if not digits:
        return value
    return ("+" if value.startswith("+") else "") + digits


def conversation_key(raw: str) -> str:
    value = raw.strip()
    if "@" in value:
        return "e:" + value.lower()
    digits = _NON_DIGITS.sub("", value)
    if len(digits) >= _MIN_PHONE_DIGITS:
        return "p:" + digits[-_MATCH_DIGITS:]
    if digits and digits == value.lstrip("+"):
        return "p:" + digits
    return "n:" + value.lower()
