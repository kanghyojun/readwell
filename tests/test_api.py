"""viewserver 통합 테스트: /read /view/{id} /view/{id}/ask.

extract(네트워크)와 agent(실호출)는 가짜로 주입한다.
"""

from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.models import Extraction, Paragraph, Section
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


def test_read_returns_view_url_and_persists_session(tmp_path):
    client = _client(tmp_path)
    r = client.post("/read", json={"url": "https://x/y"})
    assert r.status_code == 200
    data = r.json()
    assert data["viewUrl"] == f"http://test/view/{data['id']}"
    assert (tmp_path / "data" / f"{data['id']}.json").exists()
    # 분석이 붙었으니 vault md도 저장
    assert list((tmp_path / "vault").glob("*.md"))


def test_read_reports_invalid_locators(tmp_path):
    class BadAgent:
        async def run(self, prompt, schema):
            return {
                "scan": "s",
                "gist": [{"section": "x", "oneLine": "y", "locator": "s9-p9"}],
                "claims": [],
                "questions": [],
                "critique": [],
            }

    client = _client(tmp_path, agent=BadAgent())
    r = client.post("/read", json={"url": "https://x/y"})
    assert r.status_code == 200
    assert r.json()["invalidLocators"] == ["s9-p9"]


def test_view_renders_original_with_anchors_and_analysis(tmp_path):
    client = _client(tmp_path)
    sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
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
    client = _client(tmp_path)
    sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
    r = client.post(f"/view/{sid}/ask", json={"question": "이거 뭐야", "kind": "gloss"})
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
