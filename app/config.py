"""설정. 환경변수로 덮어쓸 수 있다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_VAULT = Path.home() / "src" / "mac-handoff" / "readwell"


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    vault_dir: Path = field(default_factory=lambda: _DEFAULT_VAULT)
    base_url: str = ""  # 뷰 URL 접두. 예: http://100.99.117.44:2100
    model: str | None = None
    write_vault: bool = True
    host: str = "0.0.0.0"
    port: int = 2100

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            data_dir=Path(os.environ.get("READWELL_DATA_DIR", REPO_ROOT / "data")),
            vault_dir=Path(os.environ.get("READWELL_VAULT_DIR", _DEFAULT_VAULT)),
            base_url=os.environ.get("READWELL_BASE_URL", ""),
            model=os.environ.get("READWELL_MODEL") or None,
            write_vault=os.environ.get("READWELL_WRITE_VAULT", "1") != "0",
            host=os.environ.get("READWELL_HOST", "0.0.0.0"),
            port=int(os.environ.get("READWELL_PORT", "2100")),
        )
