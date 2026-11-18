"""Minimal vCard 2.1 / 3.0 reader: names, phone numbers and email addresses."""

from __future__ import annotations

import quopri
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class VCard:
    name: str
    phones: tuple[str, ...]
    emails: tuple[str, ...]

    @property
    def addresses(self) -> tuple[str, ...]:
        return self.phones + self.emails


def parse_vcards(data: str) -> list[VCard]:
    cards: list[VCard] = []
    current: list[str] | None = None
    for line in _unfold(data):
        marker = line.strip().upper()
        if marker == "BEGIN:VCARD":
            current = []
        elif marker == "END:VCARD":
            if current is not None:
                cards.append(_parse_card(current))
            current = None
        elif current is not None:
            current.append(line)
    return cards


def _is_quoted_printable(line: str) -> bool:
    return "QUOTED-PRINTABLE" in line.split(":", 1)[0].upper()


def _unfold(data: str) -> list[str]:
    out: list[str] = []
    for line in data.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if out and line[:1] in (" ", "\t"):
            out[-1] += line[1:]
        elif out and out[-1].endswith("=") and _is_quoted_printable(out[-1]):
            # vCard 2.1 quoted-printable soft line break.
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return out


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _decode_value(value: str, params: list[str]) -> str:
    charset = next((p.split("=", 1)[1] for p in params if p.startswith("CHARSET=")), "utf-8")
    if any("QUOTED-PRINTABLE" in p for p in params):
        raw = quopri.decodestring(value.encode("utf-8"))
        try:
            return raw.decode(charset, "replace")
        except LookupError:
            return raw.decode("utf-8", "replace")
    return _unescape(value)


def _name_from_n(value: str) -> str:
    parts = [p.strip() for p in value.split(";")]
    family = parts[0] if parts else ""
    given = parts[1] if len(parts) > 1 else ""
    middle = parts[2] if len(parts) > 2 else ""
    return " ".join(p for p in (given, middle, family) if p)


def _parse_card(lines: list[str]) -> VCard:
    full_name = ""
    structured_name = ""
    phones: list[str] = []
    emails: list[str] = []
    for line in lines:
        if ":" not in line:
            continue
        head, raw_value = line.split(":", 1)
        parts = head.split(";")
        prop = parts[0].upper().rsplit(".", 1)[-1]
        params = [p.upper() for p in parts[1:]]
        value = _decode_value(raw_value, params).strip()
        if not value:
            continue
        match prop:
            case "FN":
                full_name = value
            case "N":
                structured_name = _name_from_n(value)
            case "TEL":
                phones.append(value)
            case "EMAIL":
                emails.append(value)
            case _:
                pass
    return VCard(name=full_name or structured_name, phones=tuple(phones), emails=tuple(emails))
