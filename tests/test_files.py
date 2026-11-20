from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from phonebridge.shell.app import DesktopBridge
from phonebridge.shell.files import (
    AttachmentError,
    AttachmentFiles,
    attachment_path,
    dropfiles,
    image_dib,
    safe_filename,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(out, format="PNG")
    return out.getvalue()


class FakeFetcher:
    def __init__(self, data: bytes, content_type: str) -> None:
        self.data = data
        self.content_type = content_type
        self.paths: list[str] = []

    def fetch(self, path: str) -> tuple[bytes, str]:
        self.paths.append(path)
        return self.data, self.content_type


class FakeClipboard:
    def __init__(self) -> None:
        self.files: list[Path] = []
        self.dib: bytes | None = None

    def put_files(self, paths: Sequence[Path], *, image_dib: bytes | None) -> None:
        self.files = list(paths)
        self.dib = image_dib


class FakeDialog:
    def __init__(self, answer: Path | None) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def ask(self, suggested_name: str, directory: Path) -> Path | None:
        self.asked.append(suggested_name)
        return self.answer


def make_files(
    tmp_path: Path, fetcher: FakeFetcher, dialog: FakeDialog | None = None
) -> tuple[AttachmentFiles, FakeClipboard]:
    clipboard = FakeClipboard()
    files = AttachmentFiles(
        fetcher,
        clipboard,
        dialog or FakeDialog(None),
        clipboard_dir=tmp_path / "clipboard",
        downloads_dir=tmp_path,
    )
    return files, clipboard


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_0001.HEIC", "IMG_0001.HEIC"),
        ('a/b\\c:d*e?"f<g>h|.pdf', "a_b_c_d_e__f_g_h_.pdf"),
        ("..", "attachment"),
        ("CON.txt", "attachment_CON.txt"),
        ("x" * 200 + ".jpeg", "x" * 115 + ".jpeg"),
    ],
)
def test_safe_filename(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("/api/attachments/att1?thumb=1", "/api/attachments/att1"),
        ("/api/attachments/a%20b?token=t", "/api/attachments/a%20b"),
    ],
)
def test_attachment_path_keeps_only_the_full_file(url: str, path: str) -> None:
    assert attachment_path(url) == path


@pytest.mark.parametrize(
    "url", ["http://evil.test/api/attachments/x", "/api/chats", "/api/attachments/", "/api/attachments/a/b"]
)
def test_attachment_path_rejects_anything_else(url: str) -> None:
    with pytest.raises(AttachmentError):
        attachment_path(url)


def test_dropfiles_is_a_wide_double_nul_list(tmp_path: Path) -> None:
    payload = dropfiles([tmp_path / "a.jpg"])
    offset, _, _, _, wide = struct.unpack("<IiiII", payload[:20])
    assert (offset, wide) == (20, 1)
    assert payload[20:].decode("utf-16-le") == f"{tmp_path / 'a.jpg'}\0\0"


def test_image_dib_is_a_headerless_bitmap() -> None:
    dib = image_dib(png_bytes())
    assert dib is not None
    header_size, width, height = struct.unpack("<Iii", dib[:12])
    assert (header_size, width, height) == (40, 3, 2)
    assert image_dib(b"not a picture") is None


def test_copying_an_image_puts_the_file_and_picture_on_the_clipboard(tmp_path: Path) -> None:
    fetcher = FakeFetcher(png_bytes(), "image/png")
    files, clipboard = make_files(tmp_path, fetcher)
    (tmp_path / "clipboard").mkdir()
    (tmp_path / "clipboard" / "old.pdf").write_bytes(b"old")

    assert files.copy("/api/attachments/att1?thumb=1", "Photo.png") == "image"

    assert fetcher.paths == ["/api/attachments/att1"]
    assert clipboard.files == [tmp_path / "clipboard" / "Photo.png"]
    assert clipboard.files[0].read_bytes() == fetcher.data
    assert clipboard.dib is not None
    assert sorted(p.name for p in (tmp_path / "clipboard").iterdir()) == ["Photo.png"]


def test_copying_a_document_puts_only_the_file(tmp_path: Path) -> None:
    files, clipboard = make_files(tmp_path, FakeFetcher(b"%PDF", "application/pdf"))
    assert files.copy("/api/attachments/doc", "plan.pdf") == "file"
    assert (clipboard.files[0].name, clipboard.dib) == ("plan.pdf", None)


def test_saving_writes_where_the_user_chose(tmp_path: Path) -> None:
    target = tmp_path / "chosen.pdf"
    dialog = FakeDialog(target)
    files, _ = make_files(tmp_path, FakeFetcher(b"%PDF", "application/pdf"), dialog)
    assert files.save("/api/attachments/doc", "my:plan.pdf") == str(target)
    assert (dialog.asked, target.read_bytes()) == (["my_plan.pdf"], b"%PDF")


def test_cancelling_save_fetches_nothing(tmp_path: Path) -> None:
    fetcher = FakeFetcher(b"x", "application/pdf")
    files, _ = make_files(tmp_path, fetcher)
    assert files.save("/api/attachments/doc", "plan.pdf") is None
    assert fetcher.paths == []


def test_bridge_reports_errors_to_the_page(tmp_path: Path) -> None:
    files, _ = make_files(tmp_path, FakeFetcher(b"x", "image/png"))
    bridge = DesktopBridge(files)
    assert bridge.copy_attachment("/api/chats", "x") == {
        "ok": False,
        "error": "Not an attachment of this app.",
    }
    assert bridge.save_attachment("/api/attachments/a", "x") == {"ok": True, "path": None}
