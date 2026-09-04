"""store 테스트: 세션 json 왕복, 대화 누적, vault md 렌더, 프리뷰 저장."""

from app.models import (
    Analysis,
    ChatTurn,
    Extraction,
    Paragraph,
    Preview,
    PreviewSession,
    Section,
    Session,
)
from app.store import (
    append_turn,
    fail_stale_previews,
    list_sessions,
    load_preview,
    load_session,
    purge_previews,
    render_markdown,
    save_preview,
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


# --- 세션 목록 ---------------------------------------------------------------


def test_list_sessions_newest_first(tmp_path):
    """목록 페이지는 최근에 읽은 글이 위로 온다."""
    for sid, created in [
        ("old", "2026-07-01T09:00:00"),
        ("new", "2026-07-23T18:00:00"),
        ("mid", "2026-07-10T12:00:00"),
    ]:
        s = _session(sid)
        s.created_at = created
        save_session(s, data_dir=tmp_path)

    assert [s.id for s in list_sessions(data_dir=tmp_path)] == ["new", "mid", "old"]


def test_list_sessions_skips_corrupt_files(tmp_path):
    save_session(_session("ok"), data_dir=tmp_path)
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")

    assert [s.id for s in list_sessions(data_dir=tmp_path)] == ["ok"]


def test_list_sessions_empty_when_no_data_dir(tmp_path):
    assert list_sessions(data_dir=tmp_path / "없음") == []


# --- 프리뷰 저장 -------------------------------------------------------------


def _preview_session(pid: str = "p1", *, created_at: str = "2026-09-04T10:00:00.000") -> PreviewSession:
    return PreviewSession(
        id=pid,
        url="https://example.com/post",
        title="긴 글 잘 읽기 실험",
        created_at=created_at,
        status="done",
        preview=Preview.model_validate(
            {
                "about": "원문 회귀 색인에 관한 글이다.",
                "kind": "주장글",
                "claimShape": "요약보다 색인이 낫다고 주장한다.",
                "evidence": "저자 경험",
                "audience": "긴 글을 많이 읽는 사람",
                "notCovered": "구현 세부는 다루지 않는다.",
                "quotes": [{"text": "본문.", "locator": "s1-p1"}],
            }
        ),
        char_count=3,
        read_minutes=1,
    )


def test_preview_save_and_load_roundtrip(tmp_path):
    save_preview(_preview_session(), data_dir=tmp_path)
    loaded = load_preview("p1", data_dir=tmp_path)
    assert loaded.title == "긴 글 잘 읽기 실험"
    assert loaded.preview.claim_shape == "요약보다 색인이 낫다고 주장한다."
    assert loaded.preview.quotes[0].locator == "s1-p1"


def test_load_missing_preview_raises(tmp_path):
    save_preview(_preview_session(), data_dir=tmp_path)
    try:
        load_preview("nope", data_dir=tmp_path)
    except FileNotFoundError:
        return
    raise AssertionError("FileNotFoundError가 나야 한다")


def test_previews_do_not_appear_in_session_list(tmp_path):
    """프리뷰는 읽은 글이 아니다. 목록에 오르면 목록의 뜻이 흐려진다."""
    save_preview(_preview_session(), data_dir=tmp_path)
    save_session(_session("real1"), data_dir=tmp_path)
    assert [s.id for s in list_sessions(data_dir=tmp_path)] == ["real1"]


def test_purge_removes_previews_past_ttl(tmp_path):
    import datetime

    now = datetime.datetime.now()
    old = (now - datetime.timedelta(days=30)).isoformat(timespec="milliseconds")
    fresh = (now - datetime.timedelta(days=1)).isoformat(timespec="milliseconds")
    save_preview(_preview_session("old1", created_at=old), data_dir=tmp_path)
    save_preview(_preview_session("new1", created_at=fresh), data_dir=tmp_path)

    assert purge_previews(data_dir=tmp_path, older_than_days=7) == 1
    assert load_preview("new1", data_dir=tmp_path).id == "new1"
    try:
        load_preview("old1", data_dir=tmp_path)
    except FileNotFoundError:
        return
    raise AssertionError("오래된 프리뷰가 남아 있다")


def test_purge_removes_promoted_previews_too(tmp_path):
    """승격했어도 TTL이 지나면 지운다. 뷰가 이미 원문을 갖고 있다."""
    import datetime

    old = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat(
        timespec="milliseconds"
    )
    session = _preview_session("old1", created_at=old)
    session.promoted_to = "abc123"
    save_preview(session, data_dir=tmp_path)
    assert purge_previews(data_dir=tmp_path, older_than_days=7) == 1


def test_fail_stale_previews_marks_interrupted_ones(tmp_path):
    """서버가 재시작되면 진행 중이던 프리뷰는 영영 안 끝난다. 페이지가 끝없이 폴링한다."""
    stale = _preview_session("mid1")
    stale.status = "previewing"
    save_preview(stale, data_dir=tmp_path)

    assert fail_stale_previews(data_dir=tmp_path) == 1
    reloaded = load_preview("mid1", data_dir=tmp_path)
    assert reloaded.status == "failed"
    assert "재시작" in reloaded.error


def test_fail_stale_previews_leaves_done_ones_alone(tmp_path):
    save_preview(_preview_session("done1"), data_dir=tmp_path)
    assert fail_stale_previews(data_dir=tmp_path) == 0
    assert load_preview("done1", data_dir=tmp_path).status == "done"
