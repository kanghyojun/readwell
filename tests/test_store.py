"""store 테스트: 세션 json 왕복, 대화 누적, vault md 렌더."""

from app.models import Analysis, ChatTurn, Extraction, Paragraph, Section, Session
from app.store import (
    append_turn,
    load_session,
    render_markdown,
    save_session,
    slugify_title,
    write_vault_md,
)


def _session(sid: str = "abc123") -> Session:
    ext = Extraction(
        title="긴 글 잘 읽기 실험",
        url="https://example.com/post",
        markdown="# 긴 글 잘 읽기 실험\n\n본문.",
        sections=[
            Section(
                locator="s1",
                title="긴 글 잘 읽기 실험",
                paragraphs=[Paragraph(locator="s1-p1", text="본문.")],
            )
        ],
    )
    analysis = Analysis.model_validate(
        {
            "scan": "이 글은 원문 회귀 색인에 관한 글이다.",
            "gist": [
                {"section": "도입", "oneLine": "요약은 원문을 평면화한다", "locator": "s1-p1"}
            ],
            "claims": [
                {"claim": "요약은 위험", "evidence": "평면화", "locator": "s1-p1"}
            ],
            "questions": [
                {"q": "핵심 주장은?", "answerQuote": "본문.", "locator": "s1-p1"}
            ],
            "critique": [],
        }
    )
    return Session(
        id=sid,
        url="https://example.com/post",
        title="긴 글 잘 읽기 실험",
        created_at="2026-07-11T09:00:00",
        extraction=ext,
        analysis=analysis,
        conversation=[],
    )


def test_save_and_load_roundtrip(tmp_path):
    s = _session()
    path = save_session(s, data_dir=tmp_path)
    assert path.exists()
    loaded = load_session("abc123", data_dir=tmp_path)
    assert loaded.title == s.title
    assert loaded.analysis.gist[0].one_line == "요약은 원문을 평면화한다"
    assert loaded.extraction.sections[0].paragraphs[0].locator == "s1-p1"


def test_load_missing_session_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_session("nope", data_dir=tmp_path)


def test_append_turn_accumulates_conversation(tmp_path):
    s = _session()
    save_session(s, data_dir=tmp_path)
    turn = ChatTurn(
        question="이 개념 풀어줘",
        answer="원문 s1-p1에 따르면 …",
        locators=["s1-p1"],
        kind="gloss",
    )
    updated = append_turn("abc123", turn, data_dir=tmp_path)
    assert len(updated.conversation) == 1
    reloaded = load_session("abc123", data_dir=tmp_path)
    assert reloaded.conversation[0].question == "이 개념 풀어줘"
    assert reloaded.conversation[0].kind == "gloss"


def test_slugify_keeps_korean_and_replaces_spaces():
    assert slugify_title("긴 글 잘 읽기 실험") == "긴-글-잘-읽기-실험"


def test_slugify_strips_forbidden_filename_chars():
    slug = slugify_title('제목/부제: "인용"')
    for ch in '/\\:*?"<>|':
        assert ch not in slug


def test_slugify_empty_falls_back():
    assert slugify_title("   ") == "untitled"


def test_render_markdown_includes_sections_and_anchors():
    md = render_markdown(_session())
    assert "# 긴 글 잘 읽기 실험" in md
    assert "https://example.com/post" in md
    assert "원문 회귀 색인" in md  # scan
    assert "요약은 원문을 평면화한다" in md  # gist oneLine
    assert "s1-p1" in md  # locator 앵커 표기


def test_write_vault_md_uses_date_and_slug(tmp_path):
    s = _session()
    path = write_vault_md(s, vault_dir=tmp_path, date="2026-07-11")
    assert path.name == "2026-07-11-긴-글-잘-읽기-실험.md"
    assert path.exists()
    assert "긴 글 잘 읽기 실험" in path.read_text(encoding="utf-8")
