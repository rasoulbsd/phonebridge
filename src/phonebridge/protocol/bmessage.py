"""The bMessage text format MAP uses to carry a single message."""

from __future__ import annotations

from dataclasses import dataclass

from phonebridge.protocol.vcard import VCard, parse_vcards


@dataclass(slots=True, frozen=True)
class BMessage:
    status: str
    msg_type: str
    folder: str
    originator: VCard | None
    recipients: tuple[VCard, ...]
    text: str


def parse_bmessage(data: str) -> BMessage:
    stack: list[str] = []
    props: dict[str, str] = {}
    originator: VCard | None = None
    recipients: list[VCard] = []
    parts: list[str] = []
    vcard_lines: list[str] | None = None
    msg_lines: list[str] | None = None

    for line in data.replace("\r\n", "\n").split("\n"):
        if msg_lines is not None:
            if line.strip().upper() == "END:MSG":
                parts.append("\n".join(msg_lines))
                msg_lines = None
            else:
                msg_lines.append(line)
            continue
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("BEGIN:"):
            block = upper.removeprefix("BEGIN:")
            if block == "MSG":
                msg_lines = []
                continue
            if block == "VCARD":
                vcard_lines = []
            stack.append(block)
        elif upper.startswith("END:"):
            block = upper.removeprefix("END:")
            if block == "VCARD" and vcard_lines is not None:
                cards = parse_vcards("BEGIN:VCARD\n" + "\n".join(vcard_lines) + "\nEND:VCARD")
                if cards:
                    if "BENV" in stack:
                        recipients.append(cards[0])
                    else:
                        originator = cards[0]
                vcard_lines = None
            if stack and stack[-1] == block:
                stack.pop()
        elif vcard_lines is not None:
            vcard_lines.append(line)
        elif ":" in stripped and stack and stack[-1] == "BMSG":
            key, value = stripped.split(":", 1)
            props[key.upper()] = value

    return BMessage(
        status=props.get("STATUS", ""),
        msg_type=props.get("TYPE", ""),
        folder=props.get("FOLDER", ""),
        originator=originator,
        recipients=tuple(recipients),
        text="".join(parts),
    )


def build_bmessage(recipient: str, text: str, *, msg_type: str = "SMS_GSM") -> bytes:
    field = "EMAIL" if "@" in recipient else "TEL"
    content = f"BEGIN:MSG\r\n{text}\r\nEND:MSG\r\n"
    head = [
        "BEGIN:BMSG",
        "VERSION:1.0",
        "STATUS:READ",
        f"TYPE:{msg_type}",
        "FOLDER:TELECOM/MSG/OUTBOX",
        "BEGIN:BENV",
        "BEGIN:VCARD",
        "VERSION:2.1",
        "N:",
        f"{field}:{recipient}",
        "END:VCARD",
        "BEGIN:BBODY",
        "CHARSET:UTF-8",
        # LENGTH covers BEGIN:MSG through END:MSG, line breaks included (MAP spec).
        f"LENGTH:{len(content.encode('utf-8'))}",
    ]
    tail = "END:BBODY\r\nEND:BENV\r\nEND:BMSG\r\n"
    return ("\r\n".join(head) + "\r\n" + content + tail).encode("utf-8")
