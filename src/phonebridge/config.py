"""Settings the user changes inside the app (the Mac connection), kept in the data folder."""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

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


class ConfigStore:
    """Blocking file I/O: call from a worker thread (asyncio.to_thread)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> UserConfig:
        if not self._path.exists():
            return UserConfig()
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
            stored = _Stored.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            logger.warning("ignoring unreadable settings file %s: %s", self._path, exc)
            return UserConfig()
        if stored.mac is None:
            return UserConfig()
        try:
            password = dpapi.unprotect(base64.b64decode(stored.mac.password_dpapi)).decode("utf-8")
        except (dpapi.DpapiError, ValueError) as exc:
            logger.warning("saved Mac password can't be decrypted (different Windows account?): %s", exc)
            return UserConfig()
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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self._path)
