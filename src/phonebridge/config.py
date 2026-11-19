"""Settings the user changes inside the app (the Mac connection), kept in the data folder."""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from phonebridge import dpapi

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class MacConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    password: SecretStr


class UserConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mac: MacConnection | None = None


class _StoredMac(BaseModel):
    url: str
    password_dpapi: str


class _Stored(BaseModel):
    mac: _StoredMac | None = None


class ConfigBackup(Protocol):
    """A second place for the settings, so losing the data folder doesn't lose the Mac connection."""

    def read(self) -> str | None: ...

    def write(self, text: str) -> None: ...


class ConfigStore:
    """Blocking file I/O: call from a worker thread (asyncio.to_thread)."""

    def __init__(self, path: Path, backup: ConfigBackup | None = None) -> None:
        self._path = path
        self._backup = backup

    def _read_file(self) -> str | None:
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("can't read settings file %s: %s", self._path, exc)
            return None

    def _write_file(self, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self._path)

    def load(self) -> UserConfig:
        from_file = self._read_file()
        if from_file is not None and (config := self._parse(from_file, "settings file")) is not None:
            return config
        from_backup = self._backup.read() if self._backup is not None else None
        if from_backup is not None and (config := self._parse(from_backup, "settings backup")) is not None:
            if config.mac is not None:
                logger.warning(
                    "settings file was missing or damaged; restored the Mac connection from the backup"
                )
                self._write_file(from_backup)
            return config
        if from_file is None:
            logger.info("no saved Mac connection")
        return UserConfig()

    @staticmethod
    def _parse(text: str, source: str) -> UserConfig | None:
        """None when the text can't be used (damaged, or encrypted for another Windows account)."""
        try:
            raw: Any = json.loads(text)
            stored = _Stored.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("ignoring unreadable %s: %s", source, exc)
            return None
        if stored.mac is None:
            return UserConfig()
        try:
            password = dpapi.unprotect(base64.b64decode(stored.mac.password_dpapi)).decode("utf-8")
        except (dpapi.DpapiError, ValueError) as exc:
            logger.warning(
                "saved Mac password in the %s can't be decrypted (different Windows account?): %s",
                source,
                exc,
            )
            return None
        return UserConfig(mac=MacConnection(url=stored.mac.url, password=SecretStr(password)))

    def save(self, config: UserConfig) -> None:
        stored = _Stored(
            mac=_StoredMac(
                url=config.mac.url,
                password_dpapi=base64.b64encode(
                    dpapi.protect(config.mac.password.get_secret_value().encode("utf-8"))
                ).decode("ascii"),
            )
            if config.mac
            else None
        )
        text = stored.model_dump_json(indent=2)
        self._write_file(text)
        if self._backup is not None:
            self._backup.write(text)
