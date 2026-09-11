"""번역이 읽기 흐름에 붙는 방식.

분석과 나란히 돌고, 문단이 끝나는 대로 저장된다. 뷰는 /translations를 폴링해
새로 온 문단만 DOM에 꽂는다. 분석과 달리 리로드하지 않는다. 계속 들어오는 조각마다
새로고침하면 읽던 자리를 잃기 때문이다.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.models import Extraction, Paragraph, Section
from app.store import load_session

_ANALYSIS = {
    "scan": "s",
    "gist": [{"section": "x", "oneLine": "y", "locator": "s1-p1"}],
    "claims": [{"claim": "c", "evidence": "e", "locator": "s1-p1"}],
    "questions": [{"q": "q", "answerQuote": "a", "locator": "s1-p1"}],
    "critique": [
        {
            "hiddenPremise": "h",
            "weakEvidence": "w",
            "missingCounterexample": "m",
            "locator": "s1-p1",
        }
    ],
}


def _ext(*texts: str, url: str = "https://x/y") -> Extraction:
    return Extraction(
        title="Reading Well",
        url=url,
        markdown="...",
        sections=[
            Section(
                locator="s1",
                paragraphs=[
                    Paragraph(locator=f"s1-p{i}", text=t)
                    for i, t in enumerate(texts, 1)
                ],
            )
        ],
    )


_ENGLISH = _ext(
    "Summarization flattens the source text into an index.",
    "The reader loses the argument structure entirely.",
)
_KOREAN = _ext(
    "요약은 원문을 색인으로 평면화한다. 이것이 문제의 핵심이다.",
    "독자는 논증 구조를 통째로 잃어버린다. 되돌아갈 길이 없다.",
)


class _Agent:
    """analyze와 translate를 스키마로 구분하는 가짜 러너."""

    async def run(self, prompt: str, schema: dict) -> dict:
        if set(schema.get("properties", {})) == {"text"}:
            return {"text": "번역된 문장."}
        return _ANALYSIS


def _client(tmp_path, *, extraction: Extraction, agent=None, **cfg_kwargs) -> TestClient:
    cfg = Config(
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        base_url="http://test",
        write_vault=False,
        **cfg_kwargs,
    )
    app = create_app(
        config=cfg, extractor=lambda url: extraction, agent=agent or _Agent()
    )
    return TestClient(app)


def _settled(client: TestClient, sid: str, timeout: float = 10.0) -> dict:
    """번역이 마감될 때까지 기다린다.

    translations만 보면 안 된다. 번역을 시작하기 전에도 done=True로 보이기
    때문이다(한국어 글과 구분되지 않는다). 작업이 아직 도는 중인지는 status로
    판단한다. 뷰 JS도 같은 규칙을 쓴다.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status = client.get(f"/view/{sid}/status").json()["status"]
        last = client.get(f"/view/{sid}/translations").json()
        if status not in ("extracting", "analyzing") and last["done"]:
            return last
        time.sleep(0.02)
    raise AssertionError(f"번역이 안 끝남. 마지막: {last}")


def test_english_article_gets_every_paragraph_translated(tmp_path):
    with _client(tmp_path, extraction=_ENGLISH) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        body = _settled(client, sid)
        assert body["paragraphs"] == {
            "s1-p1": "번역된 문장.",
            "s1-p2": "번역된 문장.",
        }

    session = load_session(sid, data_dir=tmp_path / "data")
    assert session.translation is not None
    assert session.translation.done


def test_korean_article_is_not_translated(tmp_path):
    with _client(tmp_path, extraction=_KOREAN) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(f"/view/{sid}/status").json()["status"] == "done":
                break
            time.sleep(0.02)
        assert client.get(f"/view/{sid}/translations").json() == {
            "done": True,
            "paragraphs": {},
            "note": None,
        }

    assert load_session(sid, data_dir=tmp_path / "data").translation is None


def test_translation_appears_in_the_page(tmp_path):
    with _client(tmp_path, extraction=_ENGLISH) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        _settled(client, sid)
        html = client.get(f"/view/{sid}").text
    assert "번역된 문장." in html
    assert 'data-tr-for="s1-p1"' in html  # JS가 나중에 꽂을 자리와 같은 표식


def test_paragraph_limit_skips_translation(tmp_path):
    long_doc = _ext(*[f"Paragraph number {i} of a very long article." for i in range(6)])
    with _client(
        tmp_path, extraction=long_doc, translate_max_paragraphs=5
    ) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        body = _settled(client, sid)
        assert body["paragraphs"] == {}
        assert "6" in body["note"] and "5" in body["note"]


def test_partial_failure_is_noted_but_rest_survives(tmp_path):
    class Flaky:
        async def run(self, prompt: str, schema: dict) -> dict:
            if set(schema.get("properties", {})) != {"text"}:
                return _ANALYSIS
            if "loses the argument" in prompt:
                raise RuntimeError("모델 오류")
            return {"text": "번역된 문장."}

    with _client(tmp_path, extraction=_ENGLISH, agent=Flaky()) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        body = _settled(client, sid)
        assert set(body["paragraphs"]) == {"s1-p1"}
        assert "1" in body["note"]


def test_translation_survives_analysis_failure(tmp_path):
    """분석이 실패해도 번역은 끝까지 간다. 둘은 서로 독립이다."""
    from app.reader import ReaderError

    class AnalysisBreaks:
        async def run(self, prompt: str, schema: dict) -> dict:
            if set(schema.get("properties", {})) == {"text"}:
                return {"text": "번역된 문장."}
            raise ReaderError("분석 실패")

    with _client(tmp_path, extraction=_ENGLISH, agent=AnalysisBreaks()) as client:
        sid = client.post("/read", json={"url": "https://x/y"}).json()["id"]
        body = _settled(client, sid)
        assert len(body["paragraphs"]) == 2


def test_stale_translation_is_closed_on_startup(tmp_path):
    """재시작으로 중단된 번역을 열어두면 뷰가 영원히 폴링한다."""
    from app.models import Session, Translation
    from app.store import save_session

    save_session(
        Session(
            id="halfway",
            url="https://x/y",
            title="t",
            created_at="2026-07-23T00:00:00",
            status="done",
            extraction=_ENGLISH,
            translation=Translation(done=False, paragraphs={"s1-p1": "번역된 문장."}),
        ),
        data_dir=tmp_path / "data",
    )
    with _client(tmp_path, extraction=_ENGLISH) as client:
        body = client.get("/view/halfway/translations").json()
        assert body["done"] is True
        assert body["paragraphs"] == {"s1-p1": "번역된 문장."}
        assert body["note"]


# --- 기존 세션 번역 ----------------------------------------------------------


def _saved(tmp_path, extraction: Extraction, sid: str = "old") -> str:
    """번역 없이 저장된 예전 세션. 번역 기능이 붙기 전에 읽은 글이 이 상태다."""
    from app.models import Session
    from app.store import save_session

    save_session(
        Session(
            id=sid,
            url="https://x/y",
            title="Reading Well",
            created_at="2026-07-23T00:00:00",
            status="done",
            extraction=extraction,
        ),
        data_dir=tmp_path / "data",
    )
    return sid


def test_translate_route_fills_an_old_session(tmp_path):
    sid = _saved(tmp_path, _ENGLISH)
    with _client(tmp_path, extraction=_ENGLISH) as client:
        assert client.post(f"/view/{sid}/translate").json()["status"] == "translating"
        body = _settled(client, sid)
        assert body["paragraphs"] == {
            "s1-p1": "번역된 문장.",
            "s1-p2": "번역된 문장.",
        }


def test_translate_route_skips_korean_article(tmp_path):
    sid = _saved(tmp_path, _KOREAN)
    with _client(tmp_path, extraction=_KOREAN) as client:
        body = client.post(f"/view/{sid}/translate").json()
        assert body["status"] == "skipped"
        assert body["note"]


def test_translate_route_404_for_unknown_session(tmp_path):
    with _client(tmp_path, extraction=_ENGLISH) as client:
        assert client.post("/view/nope/translate").status_code == 404
