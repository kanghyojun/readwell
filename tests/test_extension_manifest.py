"""확장 매니페스트가 Chrome/Firefox 양쪽에서 유효한지 검사한다.

Firefox는 매치 패턴에 포트 번호를 지원하지 않는다. 포트가 붙은 패턴은
에러 없이 조용히 "아무 URL에도 매치되지 않음"이 되어, 확장의 fetch가
호스트 권한 없이 CORS 검사에 걸린다. web-ext lint도 이걸 잡아주지 않는다.
(Bugzilla 1362809, 1468162)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

_MANIFEST = Path(__file__).resolve().parent.parent / "extension" / "manifest.json"
_EXTENSION = _MANIFEST.parent


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_host_permissions_have_no_port(manifest: dict) -> None:
    for pattern in manifest["host_permissions"]:
        host = pattern.split("://", 1)[1].split("/", 1)[0]
        assert ":" not in host, (
            f"{pattern!r}: Firefox는 매치 패턴의 포트를 무시하지 않고 매치 실패로 처리한다. "
            "포트를 빼면 해당 호스트의 모든 포트에 매치된다."
        )


def test_host_permissions_cover_local_dev(manifest: dict) -> None:
    patterns = set(manifest["host_permissions"])
    assert {"http://localhost/*", "http://127.0.0.1/*"} <= patterns


def test_storage_sync_requires_gecko_id(manifest: dict) -> None:
    """Firefox의 storage.sync는 add-on ID 없이는 동작하지 않는다."""
    assert "storage" in manifest["permissions"]
    assert manifest["browser_specific_settings"]["gecko"]["id"]


_ICON_SIZES = {"16": 16, "32": 32, "48": 48, "128": 128}


def _icon_declarations(manifest: dict) -> list[dict]:
    """아이콘을 선언하는 두 자리. 관리 페이지용과 툴바 버튼용."""
    return [manifest["icons"], manifest["action"]["default_icon"]]


def test_icons_declare_every_size(manifest: dict) -> None:
    """빠진 크기가 있으면 브라우저가 가까운 것을 늘려 써서 뭉개진다."""
    for icons in _icon_declarations(manifest):
        assert _ICON_SIZES.keys() <= icons.keys()


def test_declared_icons_are_png_of_the_declared_size(manifest: dict) -> None:
    """선언한 파일이 실재하고 크기도 선언과 같아야 한다.

    크기는 PNG 헤더에서 직접 읽는다. IHDR의 폭·높이가 빅엔디안 uint32 두 개다.
    이것 하나 보자고 Pillow를 넣을 이유가 없다.
    """
    for icons in _icon_declarations(manifest):
        for key, size in _ICON_SIZES.items():
            icon_path = _EXTENSION / icons[key]
            assert icon_path.is_file(), f"아이콘 파일이 없다: {icon_path}"

            head = icon_path.read_bytes()[:24]
            assert head[:8] == b"\x89PNG\r\n\x1a\n", f"PNG가 아니다: {icon_path}"
            assert struct.unpack(">II", head[16:24]) == (size, size)
