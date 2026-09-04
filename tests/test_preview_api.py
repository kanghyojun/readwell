"""프리뷰 엔드포인트 통합 테스트: /preview, 승격, 목록 격리.

extract(네트워크)와 agent(실호출)는 가짜로 주입한다.
"""

import time

from fastapi.testclient import TestClient

from app.config import Config
from app.extractor import ExtractionError
from app.main import create_app
from app.models import Extraction, Paragraph, Section
from app.store import load_preview, load_session

_VALID_PREVIEW = {
    "about": "원문 회귀 색인으로 긴 글을 읽는 방법을 다룬다.",
    "kind": "주장글",
    "claimShape": "요약보다 색인이 낫다고 주장한다.",
    "evidence": "저자 경험",
    "audience": "긴 글을 많이 읽는 사람",
    "notCovered": "구현 세부는 다루지 않는다.",
    "quotes": [{"text": "요약은 원문을 평면화한다.", "locator": "s1-p1"}],
}

_VALID_ANALYSIS = {
    "scan": "원문 회귀 색인에 관한 글.",
    "gist": [{"section": "도입", "oneLine": "요약은 평면화", "locator": "s1-p1"}],
    "claims": [],
    "questions": [
        {"q": "핵심 주장은?", "answerQuote": "요약은 원문을 평면화한다.", "locator": "s1-p1"}
    ],
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
                paragraphs=[
                    Paragraph(locator="s1-p1", text="요약은 원문을 평면화한다."),
                    Paragraph(locator="s1-p2", text="색인으로 격하시킨다."),
                ],
            )
        ],
    )


class FakeAgent:
    """스키마로 preview/analyze/ask/translate를 구분해 응답."""

    async def run(self, prompt, schema):
        props = schema.get("properties", {})
        if "about" in props:
            return _VALID_PREVIEW
        if "answer" in props:
            return {"answer": "원문 s1-p1에 따르면 …", "locators": ["s1-p1"]}
        if "text" in props:
            return {"text": "번역문"}
        return _VALID_ANALYSIS


class CountingExtractor:
    """추출기가 몇 번 불렸는지 센다. 승격이 재추출을 안 하는지 확인하는 데 쓴다."""

    def __init__(self):
        self.calls = 0

    def __call__(self, url: str) -> Extraction:
        self.calls += 1
        return _ext(url)


def _client(tmp_path, *, agent=None, extractor=None) -> TestClient:
    cfg = Config(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        base_url="http://test",
        write_vault=True,
    )
    app = create_app(
        config=cfg,
        extractor=extractor or (lambda url: _ext(url)),
        agent=agent or FakeAgent(),
    )
    return TestClient(app)


def _await_preview(client: TestClient, pid: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/preview/{pid}/status").json()["status"] in ("done", "failed"):
            return pid
        time.sleep(0.02)
    raise AssertionError(f"프리뷰가 안 끝남: {pid}")


def _preview(client: TestClient, url: str = "https://x/y") -> str:
    return _await_preview(client, client.post("/preview", json={"url": url}).json()["id"])


def _await_session_done(client: TestClient, sid: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/view/{sid}/status").json()["status"] == "done":
            return sid
        time.sleep(0.02)
    raise AssertionError(f"세션이 안 끝남: {sid}")


# --- 생성 --------------------------------------------------------------------


def test_preview_returns_url_immediately(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/preview", json={"url": "https://x/y"})
        assert r.status_code == 200
        data = r.json()
        assert data["previewUrl"] == f"http://test/preview/{data['id']}"
        assert data["status"] == "extracting"
        _await_preview(client, data["id"])


def test_preview_page_shows_character_card(tmp_path):
    with _client(tmp_path) as client:
        pid = _preview(client)
        html = client.get(f"/preview/{pid}").text
    assert "원문 회귀 색인으로 긴 글을 읽는 방법을 다룬다." in html
    assert "주장글" in html
    assert "구현 세부는 다루지 않는다." in html
    assert "요약은 원문을 평면화한다." in html  # 저자의 문장
    assert "도입" in html  # 인용의 섹션 제목


def test_preview_page_shows_length_and_reading_time(tmp_path):
    with _client(tmp_path) as client:
        pid = _preview(client)
        stored = load_preview(pid, data_dir=tmp_path / "data")
    assert stored.char_count > 0
    assert stored.read_minutes >= 1


def test_preview_page_never_shows_the_full_article(tmp_path):
    """원문을 실으면 판단하러 왔다가 그냥 읽게 된다. 흐름을 나눈 값어치가 사라진다."""
    with _client(tmp_path) as client:
        pid = _preview(client)
        html = client.get(f"/preview/{pid}").text
    assert "색인으로 격하시킨다." not in html  # 인용되지 않은 문단


def test_extraction_failure_leaves_reason(tmp_path):
    def boom(url):
        raise ExtractionError("본문을 못 찾음")

    with _client(tmp_path, extractor=boom) as client:
        pid = _preview(client)
        assert client.get(f"/preview/{pid}/status").json()["status"] == "failed"
        assert "본문을 못 찾음" in client.get(f"/preview/{pid}").text


def test_unknown_preview_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/preview/nope").status_code == 404


# --- 승격 --------------------------------------------------------------------


def test_promote_creates_session_without_extracting_again(tmp_path):
    extractor = CountingExtractor()
    with _client(tmp_path, extractor=extractor) as client:
        pid = _preview(client)
        assert extractor.calls == 1

        r = client.post(f"/preview/{pid}/read")
        assert r.status_code == 200
        sid = r.json()["id"]
        assert r.json()["viewUrl"] == f"http://test/view/{sid}"
        _await_session_done(client, sid)

    assert extractor.calls == 1  # 프리뷰가 뽑아둔 원문을 그대로 물려받는다
    session = load_session(sid, data_dir=tmp_path / "data")
    assert session.extraction.sections[0].paragraphs[0].text == "요약은 원문을 평면화한다."
    assert session.analysis is not None


def test_promote_uses_preset_questions(tmp_path):
    """훑어보기에서 넘어올 때는 질문을 안 받는다. 버튼 하나로 끝나야 한다."""
    with _client(tmp_path) as client:
        pid = _preview(client)
        sid = client.post(f"/preview/{pid}/read").json()["id"]
        _await_session_done(client, sid)
    assert load_session(sid, data_dir=tmp_path / "data").questions == []


def test_promoting_twice_returns_the_same_session(tmp_path):
    with _client(tmp_path) as client:
        pid = _preview(client)
        first = client.post(f"/preview/{pid}/read").json()["id"]
        _await_session_done(client, first)
        second = client.post(f"/preview/{pid}/read").json()["id"]
    assert first == second


def test_promoted_preview_redirects_to_the_view(tmp_path):
    with _client(tmp_path) as client:
        pid = _preview(client)
        sid = client.post(f"/preview/{pid}/read").json()["id"]
        _await_session_done(client, sid)
        r = client.get(f"/preview/{pid}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/view/{sid}"


def test_promoting_unfinished_preview_is_rejected(tmp_path):
    def boom(url):
        raise ExtractionError("본문을 못 찾음")

    with _client(tmp_path, extractor=boom) as client:
        pid = _preview(client)
        assert client.post(f"/preview/{pid}/read").status_code == 409


def test_promoting_unknown_preview_returns_404(tmp_path):
    with _client(tmp_path) as client:
        assert client.post("/preview/nope/read").status_code == 404


# --- 목록 격리 ---------------------------------------------------------------


def test_previews_stay_out_of_the_reading_list(tmp_path):
    with _client(tmp_path) as client:
        _preview(client, "https://x/skipped")
        assert "긴 글 잘 읽기" not in client.get("/").text


def test_promoted_article_appears_in_the_reading_list(tmp_path):
    with _client(tmp_path) as client:
        pid = _preview(client)
        sid = client.post(f"/preview/{pid}/read").json()["id"]
        _await_session_done(client, sid)
        assert "긴 글 잘 읽기" in client.get("/").text
