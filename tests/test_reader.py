"""reader 테스트: 프롬프트 구성, 스키마 검증, locator 검증, 추가질문.

agent 호출은 FakeAgent로 대체한다(실호출 없음).
"""

import sys
import types

import pytest

from app.models import Analysis, Extraction, Paragraph, Section, Session
from app.reader import (
    PRESET_QUESTIONS,
    ClaudeAgentRunner,
    ReaderError,
    analyze,
    ask,
    build_analysis_prompt,
    build_ask_prompt,
    check_locators,
    render_located_body,
    strip_prompt_echo,
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
    "critique": [
        {
            "hiddenPremise": "요약은 늘 해롭다",
            "weakEvidence": "사례 하나",
            "missingCounterexample": "짧은 글",
            "locator": "s1-p2",
        }
    ],
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


def _both_prompts() -> list[str]:
    return [build_analysis_prompt(_ext()), build_ask_prompt(_session(), "질문")]


@pytest.mark.parametrize("prompt", _both_prompts())
def test_prompts_wrap_input_in_xml_tags(prompt: str) -> None:
    assert "<title>제목</title>" in prompt
    assert "<body>" in prompt and "</body>" in prompt


@pytest.mark.parametrize("prompt", _both_prompts())
def test_prompts_explain_what_each_tag_is_for(prompt: str) -> None:
    """태그 역할을 알려줘야 제목·URL을 본문으로 착각하지 않는다."""
    assert "<title>:" in prompt
    assert "<body>:" in prompt


@pytest.mark.parametrize("prompt", _both_prompts())
def test_prompts_have_no_symmetric_delimiters(prompt: str) -> None:
    """`=== 본문 시작 ===` 같은 구분선은 모델이 출력에서 대칭으로 흉내낸다.

    번역에서 실제로 겪었다. 프롬프트에 없던 `=== 번역 시작 ===`을 지어내
    번역문을 감싸버렸다.
    """
    assert "===" not in prompt


def test_build_ask_prompt_tags_the_user_input():
    prompt = build_ask_prompt(_session(), "이게 무슨 뜻이지?")
    assert "<user_input>\n이게 무슨 뜻이지?\n</user_input>" in prompt


def test_build_analysis_prompt_includes_presets_and_body():
    prompt = build_analysis_prompt(_ext())
    assert "[s1-p1]" in prompt
    for q in PRESET_QUESTIONS:
        assert q in prompt


def test_build_analysis_prompt_uses_my_questions_instead_of_presets():
    """읽기 전에 질문을 주면 프리셋 대신 그것만 묻는다."""
    prompt = build_analysis_prompt(_ext(), questions=["SIMD는 언제 쓰나?"])
    assert "SIMD는 언제 쓰나?" in prompt
    for q in PRESET_QUESTIONS:
        assert q not in prompt


def test_build_analysis_prompt_ignores_blank_questions():
    """빈 줄만 온 경우는 질문이 없는 것으로 보고 프리셋으로 돌아간다."""
    prompt = build_analysis_prompt(_ext(), questions=["", "   "])
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


async def test_analyze_passes_my_questions_to_prompt():
    fake = FakeAgent(_VALID_ANALYSIS)
    await analyze(_ext(), agent=fake, questions=["내가 알고 싶은 것"])
    prompt, _ = fake.calls[0]
    assert "내가 알고 싶은 것" in prompt
    assert PRESET_QUESTIONS[0] not in prompt


async def test_analyze_raises_reader_error_on_invalid_schema():
    fake = FakeAgent({"gist": []})  # 필수 scan 누락
    with pytest.raises(ReaderError):
        await analyze(_ext(), agent=fake)


@pytest.mark.parametrize("field", ["gist", "claims", "questions", "critique"])
async def test_analyze_rejects_empty_section(field):
    """빈 목록을 조용히 저장하면 탭만 빈 채로 끝나서 실패인 줄도 모른다."""
    fake = FakeAgent({**_VALID_ANALYSIS, field: []})
    with pytest.raises(ReaderError, match=field):
        await analyze(_ext(), agent=fake)


async def test_analyze_rejects_scan_only():
    """실제로 모델이 StructuredOutput에 scan 하나만 담아 끝낸 적이 있다."""
    fake = FakeAgent({"scan": "개요만 있다."})
    with pytest.raises(ReaderError):
        await analyze(_ext(), agent=fake)


# --- 출력 정리 ---------------------------------------------------------------


def test_strip_prompt_echo_removes_delimiter_lines():
    text = "=== 번역 시작 ===\n최근 설문이 있었다.\n=== 번역 끝 ==="
    assert strip_prompt_echo(text) == "최근 설문이 있었다."


def test_strip_prompt_echo_removes_delimiters_on_one_line():
    text = "=== 번역 시작 === 최근 설문이 있었다. === 번역 끝 ==="
    assert strip_prompt_echo(text) == "최근 설문이 있었다."


def test_strip_prompt_echo_removes_prompt_tags():
    assert strip_prompt_echo("<paragraph>본문</paragraph>") == "본문"
    assert strip_prompt_echo("<body>\n본문\n</body>") == "본문"


def test_strip_prompt_echo_keeps_markdown_setext_heading():
    """`====`만 있는 줄은 마크다운 제목 밑줄이다. 지우면 문서가 망가진다."""
    text = "제목\n====\n\n본문"
    assert strip_prompt_echo(text) == text


def test_strip_prompt_echo_keeps_comparison_operators():
    text = "a == b and c == d 이면 참이다."
    assert strip_prompt_echo(text) == text


@pytest.mark.asyncio
async def test_analyze_strips_prompt_echo_from_scan():
    dirty = {**_VALID_ANALYSIS, "scan": "=== 분석 시작 ===\n색인에 관한 글.\n=== 분석 끝 ==="}
    analysis = await analyze(_ext(), agent=FakeAgent(dirty))
    assert analysis.scan == "색인에 관한 글."


@pytest.mark.asyncio
async def test_analyze_leaves_quotes_alone():
    """answerQuote는 원문 인용이다. 원문에 그런 줄이 있었다면 지우는 쪽이 손해다."""
    quoted = {
        **_VALID_ANALYSIS,
        "questions": [
            {"q": "핵심 주장은?", "answerQuote": "=== 주의 ===", "locator": "s1-p1"}
        ],
    }
    analysis = await analyze(_ext(), agent=FakeAgent(quoted))
    assert analysis.questions[0].answer_quote == "=== 주의 ==="


@pytest.mark.asyncio
async def test_ask_strips_prompt_echo_from_answer():
    fake = FakeAgent({"answer": "<body>답이다.</body>", "locators": ["s1-p1"]})
    turn = await ask(_session(), "질문", agent=fake)
    assert turn.answer == "답이다."


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


# --- 실제 러너 옵션 ----------------------------------------------------------


async def test_agent_runner_leaves_room_for_structured_output(monkeypatch):
    """max_turns=1이면 모델이 구조화 출력 도구를 부르기 전에 턴이 끝난다.

    번역처럼 짧은 프롬프트에서 특히 잘 터진다(실측: 8문단 전량 실패).
    """
    captured: dict = {}

    class ResultMessage:
        is_error = False
        errors = None
        result = None
        structured_output = {"text": "ok"}

    async def fake_query(*, prompt, options):
        yield ResultMessage()

    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.ResultMessage = ResultMessage
    fake_sdk.query = fake_query
    fake_sdk.ClaudeAgentOptions = lambda **kw: captured.update(kw) or kw
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    out = await ClaudeAgentRunner().run("프롬프트", {"type": "object"})

    assert out == {"text": "ok"}
    assert captured["max_turns"] > 1
