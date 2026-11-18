from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Phonebridge" if base else Path.home() / ".phonebridge"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHONEBRIDGE_", env_file=".env", extra="ignore")

    # Bluetooth address like AA:BB:CC:DD:EE:FF; empty means "first paired iPhone".
    phone_address: str | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    # Required whenever host is not loopback (remote access).
    token: SecretStr | None = None
    data_dir: Path = Field(default_factory=_default_data_dir)
    poll_seconds: float = 5.0
    push_poll_seconds: float = 30.0
    open_window: bool = True
    demo: bool = False

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @property
    def database_path(self) -> Path:
        return self.data_dir / "phonebridge.db"

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.is_loopback or _is_unspecified(self.host) else self.host
        query = f"?token={self.token.get_secret_value()}" if self.token else ""
        return f"http://{host}:{self.port}/{query}"

    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}
        if not self.is_loopback:
            hosts.add(f"{self.host}:{self.port}")
        return hosts
