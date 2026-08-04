"""POST /read는 즉시 반환하고, 추출·분석은 백그라운드에서 돈다.

확장 팝업은 닫히면 문서가 파괴돼 진행 중인 fetch도 같이 죽는다. 그래서 서버가
작업을 넘겨받고, 뷰 탭이 상태를 폴링한다. 추출이 끝나는 즉시 원문을 보여주고
분석은 나중에 채운다.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.extractor import ExtractionError
from app.main import create_app
from app.models import Extraction, Paragraph, Section
from app.store import load_session, save_session
from app.models import Session

_ANALYSIS = {
    "scan": "요약 위험에 관한 글.",
    "gist": [{"section": "도입", "oneLine": "요약은 평면화", "locator": "s1-p1"}],
    "claims": [],
    "questions": [],
    "critique": [],
}


def _ext(url: str = "https://x/y") -> Extraction:
    return Extraction(
        title="긴 글 잘 읽기",
        url=url,
        markdown="...",
        sections=[
            Section(
                locator="s1",
                title="도입",
                paragraphs=[Paragraph(locator="s1-p1", text="요약은 원문을 평면화한다.")],
            )
        ],
    )


class _Agent:
    async def run(self, prompt, schema):
        return _ANALYSIS


class _Gate:
    """호출을 붙잡아 두는 추출기. set()을 부를 때까지 대기한다."""

    def __init__(self) -> None:
        self.released = threading.Event()
        self.entered = threading.Event()

    def __call__(self, url: str) -> Extraction:
        self.entered.set()
        assert self.released.wait(timeout=10), "게이트가 열리지 않음"
        return _ext(url)


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        base_url="http://test",
        write_vault=False,
    )


def _client(tmp_path, *, extractor=None, agent=None) -> TestClient:
    app = create_app(
        config=_cfg(tmp_path),
        extractor=extractor or (lambda url: _ext(url)),
        agent=agent or _Agent(),
    )
    return TestClient(app)


def _wait_for(client: TestClient, sid: str, target: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/view/{sid}/status").json()
        if last["status"] == target:
            return last
        time.sleep(0.02)
    raise AssertionError(f"{target} 안 됨. 마지막 상태: {last}")


def test_read_returns_immediately_while_extraction_blocks(tmp_path):
    """추출이 안 끝났어도 /read는 바로 id를 준다. 팝업이 닫혀도 되는 이유다."""
    gate = _Gate()
    with _client(tmp_path, extractor=gate) as client:
        r = client.post("/read", json={"url": "https://x/y"})
        assert r.status_code == 200
        body = r.json()
        sid = body["id"]
        assert body["viewUrl"] == f"http://test/view/{sid}"
        assert body["status"] == "extracting"

        assert gate.entered.wait(timeout=5)
        assert client.get(f"/view/{sid}/status").json()["status"] == "extracting"

        gate.released.set()
        assert _wait_for(client, sid, "done")["error"] is None

    session = load_session(sid, data_dir=tmp_path / "data")
    assert session.analysis is not None
    assert session.title == "긴 글 잘 읽기"


def test_original_is_viewable_before_analysis_finishes(tmp_path):
    """추출이 끝나면 분석을 기다리지 않고 원문부터 보여준다."""
    started = threading.Event()
    release = threading.Event()

    class SlowAgent:
        async def run(self, prompt, schema):
            # 여기서 블로킹으로 기다리면 이벤트 루프가 멈춰 뷰 요청도 막힌다.
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return _ANALYSIS

    with _client(tmp_path, agent=SlowAgent()) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        assert started.wait(timeout=5)

        assert client.get(f"/view/{sid}/status").json()["status"] == "analyzing"
        page = client.get(f"/view/{sid}")
        assert page.status_code == 200
        assert "요약은 원문을 평면화한다." in page.text

        release.set()
        _wait_for(client, sid, "done")


def test_extraction_failure_is_reported_on_the_page(tmp_path):
    def boom(url: str) -> Extraction:
        raise ExtractionError("페이월에 막힘")

    with _client(tmp_path, extractor=boom) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        state = _wait_for(client, sid, "failed")
        assert "페이월" in state["error"]

        page = client.get(f"/view/{sid}")
        assert page.status_code == 200
        assert "페이월" in page.text


def test_analysis_failure_keeps_extraction(tmp_path):
    """분석만 실패하면 추출 결과로 뷰는 연다. 기존 동작 유지."""
    from app.reader import ReaderError

    class BadAgent:
        async def run(self, prompt, schema):
            raise ReaderError("모델 응답 파싱 실패")

    with _client(tmp_path, agent=BadAgent()) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        state = _wait_for(client, sid, "done")
        assert "파싱" in state["error"]

    session = load_session(sid, data_dir=tmp_path / "data")
    assert session.extraction is not None
    assert session.analysis is None


def test_status_of_unknown_session_is_404(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/view/nope/status").status_code == 404


def test_stale_sessions_are_failed_on_startup(tmp_path):
    """서버가 재시작되면 중단된 작업이 남는다. 폴링이 영원히 돌지 않게 정리한다."""
    data_dir = tmp_path / "data"
    save_session(
        Session(
            id="stuck",
            url="https://x/y",
            title="https://x/y",
            created_at="2026-07-23T00:00:00",
            status="analyzing",
        ),
        data_dir=data_dir,
    )

    with _client(tmp_path) as client:
        state = client.get("/view/stuck/status").json()
        assert state["status"] == "failed"
        assert state["error"]


def test_ask_is_rejected_while_pending(tmp_path):
    gate = _Gate()
    with _client(tmp_path, extractor=gate) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        assert gate.entered.wait(timeout=5)

        r = client.post(f"/view/{sid}/ask", json={"question": "핵심은?"})
        assert r.status_code == 409

        gate.released.set()
        _wait_for(client, sid, "done")


@pytest.mark.parametrize("status", ["extracting", "failed"])
def test_view_renders_without_extraction(tmp_path, status):
    """추출 전이거나 실패한 세션도 뷰가 열려야 한다. 탭은 이미 떠 있다."""
    save_session(
        Session(
            id="s",
            url="https://x/y",
            title="https://x/y",
            created_at="2026-07-23T00:00:00",
            status=status,
            error="사유" if status == "failed" else None,
        ),
        data_dir=tmp_path / "data",
    )
    app = create_app(config=_cfg(tmp_path), extractor=lambda u: _ext(u), agent=_Agent())
    client = TestClient(app)
    assert client.get("/view/s").status_code == 200
