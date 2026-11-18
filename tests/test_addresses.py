import pytest

from phonebridge.domain import conversation_key, normalize_address


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+98 912 123 4567", "p:9121234567"),
        ("09121234567", "p:9121234567"),
        ("(555) 014-2000", "p:5550142000"),
        ("Sara@iCloud.com", "e:sara@icloud.com"),
        ("72975", "p:72975"),
        ("Apple", "n:apple"),
    ],
)
def test_conversation_key(raw: str, expected: str) -> None:
    assert conversation_key(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (555) 014-2000", "+15550142000"),
        ("0912 123 4567", "09121234567"),
        (" Sara@iCloud.com ", "sara@icloud.com"),
        ("Apple", "Apple"),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected
