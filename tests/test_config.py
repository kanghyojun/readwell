"""설정 테스트: 환경변수로 경로를 바꿀 때의 함정.

경로를 환경변수로 받을 때 ``~``를 안 펴면 조용히 상대경로가 된다. 에러 없이
엉뚱한 자리에 파일이 쌓이므로 눈치채기 어렵다.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Config


def test_vault_dir_expands_home(monkeypatch):
    monkeypatch.setenv("READWELL_VAULT_DIR", "~/notes/readwell")
    assert Config.from_env().vault_dir == Path.home() / "notes" / "readwell"


def test_data_dir_expands_home(monkeypatch):
    monkeypatch.setenv("READWELL_DATA_DIR", "~/readwell-data")
    assert Config.from_env().data_dir == Path.home() / "readwell-data"


def test_absolute_vault_dir_is_left_alone(monkeypatch):
    monkeypatch.setenv("READWELL_VAULT_DIR", "/tmp/vault")
    assert Config.from_env().vault_dir == Path("/tmp/vault")
