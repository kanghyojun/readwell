"""reader 테스트: 프롬프트 구성, 스키마 검증, locator 검증, 추가질문.

agent 호출은 FakeAgent로 대체한다(실호출 없음).
"""

import pytest

from app.models import Analysis, Extraction, Paragraph, Section, Session
from app.reader import (
    PRESET_QUESTIONS,
    ReaderError,
    analyze,
    ask,
    build_analysis_prompt,
    build_ask_prompt,
    check_locators,
    render_located_body,
)


class FakeAgent:
    """AgentRunner 대역. 미리 준 응답을 그대로 돌려준다."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def run(self, prompt, schema):
        self.calls.append((prompt, schema))
        return self.response


def _ext() -> Extraction:
    return Extraction(
        title="제목",
        url="https://example.com/post",
        markdown="...",
        sections=[
            Section(
                locator="s1",
                title="도입",
                paragraphs=[
                    Paragraph(locator="s1-p1", text="요약은 원문을 평면화한다."),
                    Paragraph(locator="s1-p2", text="색인으로 격하시킨다."),
                ],
            ),
            Section(
                locator="s2",
                title="방법",
                paragraphs=[Paragraph(locator="s2-p1", text="질문 기반 추출.")],
            ),
        ],
    )


_VALID_ANALYSIS = {
    "scan": "원문 회귀 색인에 관한 글.",
    "gist": [{"section": "도입", "oneLine": "요약은 평면화", "locator": "s1-p1"}],
    "claims": [{"claim": "요약 위험", "evidence": "평면화", "locator": "s1-p1"}],
    "questions": [{"q": "핵심 주장은?", "answerQuote": "요약은 원문을 평면화한다.", "locator": "s1-p1"}],
    "critique": [],
}


def _session() -> Session:
    return Session(
        id="abc",
        url="https://example.com/post",
        title="제목",
        created_at="2026-07-11T09:00:00",
        extraction=_ext(),
        analysis=Analysis.model_validate(_VALID_ANALYSIS),
        conversation=[],
    )


# --- 프리셋 / 프롬프트 -------------------------------------------------------


def test_preset_questions_are_five():
    assert len(PRESET_QUESTIONS) == 5
    assert all(isinstance(q, str) and q for q in PRESET_QUESTIONS)


def test_render_located_body_prefixes_locators():
    body = render_located_body(_ext())
    assert "[s1-p1]" in body
    assert "[s2-p1]" in body
    assert "요약은 원문을 평면화한다." in body


def test_build_analysis_prompt_includes_presets_and_body():
    prompt = build_analysis_prompt(_ext())
    assert "[s1-p1]" in prompt
    for q in PRESET_QUESTIONS:
        assert q in prompt


# --- analyze -----------------------------------------------------------------


async def test_analyze_returns_validated_analysis():
    fake = FakeAgent(_VALID_ANALYSIS)
    a = await analyze(_ext(), agent=fake)
    assert isinstance(a, Analysis)
    assert a.scan == "원문 회귀 색인에 관한 글."
    # 스키마를 함께 넘겼는지
    _, schema = fake.calls[0]
    assert schema == Analysis.json_schema()


async def test_analyze_raises_reader_error_on_invalid_schema():
    fake = FakeAgent({"gist": []})  # 필수 scan 누락
    with pytest.raises(ReaderError):
        await analyze(_ext(), agent=fake)


# --- locator 검증 ------------------------------------------------------------


def test_check_locators_flags_nonexistent():
    a = Analysis.model_validate(
        {
            "scan": "s",
            "gist": [
                {"section": "도입", "oneLine": "ok", "locator": "s1-p1"},
                {"section": "허구", "oneLine": "bad", "locator": "s9-p9"},
            ],
            "claims": [],
            "questions": [],
            "critique": [],
        }
    )
    assert check_locators(a, _ext()) == {"s9-p9"}


def test_check_locators_empty_when_all_valid():
    a = Analysis.model_validate(_VALID_ANALYSIS)
    assert check_locators(a, _ext()) == set()


# --- 추가 질문 ---------------------------------------------------------------


async def test_ask_builds_turn_with_kind_and_locators():
    fake = FakeAgent({"answer": "원문 s1-p1에 따르면 …", "locators": ["s1-p1"]})
    turn = await ask(_session(), "이 용어 풀어줘", agent=fake, kind="gloss")
    assert turn.kind == "gloss"
    assert turn.question == "이 용어 풀어줘"
    assert turn.locators == ["s1-p1"]
    prompt, _ = fake.calls[0]
    assert "이 용어 풀어줘" in prompt


def test_build_ask_prompt_feynman_mentions_understanding_check():
    prompt = build_ask_prompt(_session(), "내 이해: 색인은 지도다", kind="feynman")
    assert "이해" in prompt
    assert "[s1-p1]" in prompt  # 원문 맥락 포함


def test_build_ask_prompt_includes_prior_conversation():
    session = _session()
    from app.models import ChatTurn

    session.conversation.append(
        ChatTurn(question="이전 질문", answer="이전 답", locators=[], kind="ask")
    )
    prompt = build_ask_prompt(session, "다음 질문", kind="ask")
    assert "이전 질문" in prompt
