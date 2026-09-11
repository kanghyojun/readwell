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


def test_preview_limits_come_from_env(monkeypatch):
    monkeypatch.setenv("READWELL_PREVIEW_MAX_CHARS", "12000")
    monkeypatch.setenv("READWELL_PREVIEW_TTL_DAYS", "3")
    cfg = Config.from_env()
    assert cfg.preview_max_chars == 12000
    assert cfg.preview_ttl_days == 3


def test_preview_limits_have_defaults(monkeypatch):
    monkeypatch.delenv("READWELL_PREVIEW_MAX_CHARS", raising=False)
    monkeypatch.delenv("READWELL_PREVIEW_TTL_DAYS", raising=False)
    cfg = Config.from_env()
    assert cfg.preview_max_chars == 40000
    assert cfg.preview_ttl_days == 7


def test_model_defaults_to_opus_5(monkeypatch):
    """CLI 기본 모델에 맡기면 CLI가 업데이트될 때 모델이 몰래 바뀐다."""
    monkeypatch.delenv("READWELL_MODEL", raising=False)
    assert Config.from_env().model == "claude-opus-5"
    assert Config().model == "claude-opus-5"


def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("READWELL_MODEL", "claude-sonnet-5")
    assert Config.from_env().model == "claude-sonnet-5"
