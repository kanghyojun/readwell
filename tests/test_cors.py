"""브라우저 확장에서 오는 요청의 CORS 프리플라이트 처리.

확장이 호스트 권한을 받았으면 프리플라이트 없이 바로 POST가 온다. 그런데
Firefox는 about:debugging으로 임시 로드한 MV3 확장에 호스트 권한을 자동으로
주지 않는다(설치 프롬프트가 없어서다). 권한이 없으면 확장 요청도 일반
웹페이지처럼 CORS를 타고, `POST /read`는 application/json이라 프리플라이트가
붙는다. 서버가 이걸 거부하면 확장은 NetworkError만 보게 된다.

확장 오리진은 재로드마다 바뀌는 UUID라(moz-extension://<uuid>) 정규식으로 받는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.models import Extraction, Paragraph, Section


def _ext(url: str = "https://x/y") -> Extraction:
    return Extraction(
        title="t",
        url=url,
        markdown="...",
        sections=[
            Section(
                locator="s1",
                title="도입",
                paragraphs=[Paragraph(locator="s1-p1", text="본문.")],
            )
        ],
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    cfg = Config(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        base_url="http://test",
        write_vault=False,
    )
    return TestClient(create_app(config=cfg, extractor=lambda url: _ext(url)))


@pytest.mark.parametrize(
    "origin",
    [
        "moz-extension://8f2a1c30-0000-4000-8000-abcdef123456",
        "chrome-extension://mnopabcdefghijklabcdefghijklabcd",
    ],
)
def test_preflight_from_extension_is_allowed(client: TestClient, origin: str) -> None:
    r = client.options(
        "/read",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_preflight_from_web_page_is_rejected(client: TestClient) -> None:
    """확장이 아닌 임의 사이트에는 열어주지 않는다."""
    r = client.options(
        "/read",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_post_from_extension_carries_cors_header(client: TestClient) -> None:
    origin = "moz-extension://8f2a1c30-0000-4000-8000-abcdef123456"
    r = client.post("/read", json={"url": "https://x/y"}, headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
