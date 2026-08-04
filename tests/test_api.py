"""viewserver 통합 테스트: /read /view/{id} /view/{id}/ask.

extract(네트워크)와 agent(실호출)는 가짜로 주입한다.
"""

import time

from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.models import Extraction, Paragraph, Section
from app.reader import PRESET_QUESTIONS
from app.store import load_session

_VALID_ANALYSIS = {
    "scan": "원문 회귀 색인에 관한 글.",
    "gist": [{"section": "도입", "oneLine": "요약은 평면화", "locator": "s1-p1"}],
    "claims": [{"claim": "요약 위험", "evidence": "평면화", "locator": "s1-p1"}],
    "questions": [
        {"q": "핵심 주장은?", "answerQuote": "요약은 원문을 평면화한다.", "locator": "s1-p1"}
    ],
    "critique": [
        {
            "hiddenPremise": "요약이 늘 손실",
            "weakEvidence": "일화",
            "missingCounterexample": "좋은 요약",
            "locator": "s1-p1",
        }
    ],
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
    """스키마로 analyze/ask를 구분해 응답."""

    async def run(self, prompt, schema):
        if "answer" in schema.get("properties", {}):
            return {"answer": "원문 s1-p1에 따르면 …", "locators": ["s1-p1"]}
        return _VALID_ANALYSIS


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


def _await_done(client: TestClient, sid: str, timeout: float = 10.0) -> str:
    """백그라운드 작업이 끝날 때까지 기다린다. /read는 즉시 반환하므로 필요하다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/view/{sid}/status").json()["status"] == "done":
            return sid
        time.sleep(0.02)
    raise AssertionError(f"작업이 안 끝남: {sid}")


def _read(client: TestClient, url: str = "https://x/y") -> str:
    return _await_done(client, client.post("/read", json={"url": url}).json()["id"])


def test_read_returns_view_url_and_persists_session(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/read", json={"url": "https://x/y"})
        assert r.status_code == 200
        data = r.json()
        assert data["viewUrl"] == f"http://test/view/{data['id']}"
        _await_done(client, data["id"])
        assert (tmp_path / "data" / f"{data['id']}.json").exists()
        # 분석이 붙었으니 vault md도 저장
        assert list((tmp_path / "vault").glob("*.md"))


def test_read_with_questions_asks_them_instead_of_presets(tmp_path):
    """읽기 전에 받은 질문이 분석 프롬프트로 가고 세션에도 남는다."""

    class RecordingAgent(FakeAgent):
        def __init__(self):
            self.prompts = []

        async def run(self, prompt, schema):
            self.prompts.append(prompt)
            return await super().run(prompt, schema)

    agent = RecordingAgent()
    with _client(tmp_path, agent=agent) as client:
        sid = client.post(
            "/read",
            json={"url": "https://x/y", "questions": ["SIMD는 언제 쓰나?", "성능 이득은?"]},
        ).json()["id"]
        _await_done(client, sid)

        analysis_prompt = next(p for p in agent.prompts if "<body>" in p)
        assert "SIMD는 언제 쓰나?" in analysis_prompt
        assert "성능 이득은?" in analysis_prompt
        assert PRESET_QUESTIONS[0] not in analysis_prompt

        assert load_session(sid, data_dir=tmp_path / "data").questions == [
            "SIMD는 언제 쓰나?",
            "성능 이득은?",
        ]


def test_view_warns_about_invalid_locators(tmp_path):
    """원문에 없는 위치를 가리키는 분석 항목은 뷰에서 경고로 보인다."""

    class BadAgent:
        async def run(self, prompt, schema):
            return {
                "scan": "s",
                "gist": [{"section": "x", "oneLine": "y", "locator": "s9-p9"}],
                "claims": [],
                "questions": [],
                "critique": [],
            }

    with _client(tmp_path, agent=BadAgent()) as client:
        sid = _read(client)
        assert "s9-p9" in client.get(f"/view/{sid}").text


def test_view_renders_original_with_anchors_and_analysis(tmp_path):
    with _client(tmp_path) as client:
        sid = _read(client)
        r = client.get(f"/view/{sid}")
    assert r.status_code == 200
    html = r.text
    assert 'id="s1-p1"' in html  # 원문 문단 앵커
    assert "요약은 원문을 평면화한다." in html
    assert 'href="#s1-p1"' in html  # 분석 → 원문 앵커 링크
    assert "핵심 주장은?" in html  # 프리셋 질문


def test_view_unknown_id_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/view/nope").status_code == 404


def test_ask_appends_turn_and_persists(tmp_path):
    with _client(tmp_path) as client:
        sid = _read(client)
        r = client.post(
            f"/view/{sid}/ask", json={"question": "이거 뭐야", "kind": "gloss"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["locators"] == ["s1-p1"]
        assert body["kind"] == "gloss"

    session = load_session(sid, data_dir=tmp_path / "data")
    assert session.conversation[-1].question == "이거 뭐야"
    assert session.conversation[-1].kind == "gloss"


def test_ask_unknown_id_returns_404(tmp_path):
    client = _client(tmp_path)
    r = client.post("/view/nope/ask", json={"question": "q", "kind": "ask"})
    assert r.status_code == 404


# --- 읽은 글 목록 ------------------------------------------------------------


def test_index_lists_read_articles_with_view_links(tmp_path):
    with _client(tmp_path) as client:
        sid = _read(client, "https://x/y")
        html = client.get("/").text

    assert "긴 글 잘 읽기" in html
    assert f"/view/{sid}" in html


def test_index_newest_first(tmp_path):
    with _client(tmp_path) as client:
        first = _read(client, "https://x/first")
        second = _read(client, "https://x/second")
        html = client.get("/").text

    assert html.index(f"/view/{second}") < html.index(f"/view/{first}")


def test_index_says_so_when_nothing_read_yet(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "아직" in r.text


def _english_ext(url: str = "https://x/en") -> Extraction:
    return Extraction(
        title="Reading Well",
        url=url,
        markdown="...",
        sections=[
            Section(
                locator="s1",
                paragraphs=[
                    Paragraph(
                        locator="s1-p1",
                        text="Summarization flattens the source text into an index.",
                    )
                ],
            )
        ],
    )


def test_index_offers_translation_for_untranslated_foreign_article(tmp_path):
    """번역 없이 남은 외국어 글은 목록에서 바로 번역을 걸 수 있어야 한다."""
    with _client(tmp_path, extractor=lambda url: _english_ext(url)) as client:
        sid = _read(client, "https://x/en")
        html = client.get("/").text
    assert f'data-translate="{sid}"' in html


def test_index_has_no_translate_button_for_korean_article(tmp_path):
    with _client(tmp_path) as client:  # _ext()는 한국어라 번역 대상이 아니다
        sid = _read(client)
        html = client.get("/").text
    assert f'data-translate="{sid}"' not in html


def test_view_links_back_to_the_list(tmp_path):
    with _client(tmp_path) as client:
        sid = _read(client)
        html = client.get(f"/view/{sid}").text
    assert 'class="to-index"' in html
