"""preview 테스트: 프롬프트 구성, 스키마 검증, 인용 검증, 읽기 시간.

agent 호출은 FakeAgent로 대체한다(실호출 없음).
"""

import pytest

from app.models import Extraction, Paragraph, Preview, Section
from app.preview import (
    PreviewError,
    build_preview_prompt,
    estimate_reading,
    make_preview,
    render_skeleton,
    verify_quotes,
)


class FakeAgent:
    """AgentRunner 대역. 미리 준 응답을 그대로 돌려준다."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def run(self, prompt, schema):
        self.calls.append((prompt, schema))
        return self.response


_VALID_PREVIEW = {
    "about": "SIMD로 문자열 처리를 빠르게 하는 방법을 다룬다.",
    "kind": "튜토리얼",
    "claimShape": "스칼라 루프보다 SIMD가 낫다고 주장한다.",
    "evidence": "저자가 돌린 벤치마크",
    "audience": "C를 아는 백엔드 개발자",
    "notCovered": "GPU 가속은 다루지 않는다.",
    "quotes": [{"text": "요약은 원문을 평면화한다.", "locator": "s1-p1"}],
}


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
                title="성능 측정",
                paragraphs=[
                    Paragraph(locator="s2-p1", text="벤치마크를 돌렸다."),
                    Paragraph(locator="s2-p2", text="세 배 빨랐다."),
                ],
            ),
        ],
    )


# --- 프롬프트 ----------------------------------------------------------------


def test_prompt_wraps_input_in_xml_tags_with_locators():
    prompt = build_preview_prompt(_ext())
    assert "<title>제목</title>" in prompt
    assert "<source_url>https://example.com/post</source_url>" in prompt
    assert "<body>" in prompt and "</body>" in prompt
    assert "[s1-p1] 요약은 원문을 평면화한다." in prompt


def test_prompt_forbids_giving_away_conclusions():
    """프리뷰는 판단 재료지 답이 아니다. 프롬프트가 그걸 못 박아야 한다."""
    prompt = build_preview_prompt(_ext())
    assert "결론" in prompt


def test_long_body_falls_back_to_skeleton():
    """상한을 넘으면 섹션마다 첫 문단만 넣는다. 두 번째 문단은 빠진다."""
    prompt = build_preview_prompt(_ext(), max_chars=10)
    assert "[s1-p1]" in prompt
    assert "[s2-p1]" in prompt
    assert "[s1-p2]" not in prompt
    assert "[s2-p2]" not in prompt


def test_skeleton_keeps_every_section_title():
    skeleton = render_skeleton(_ext())
    assert "도입" in skeleton
    assert "성능 측정" in skeleton


# --- 읽기 시간 ---------------------------------------------------------------


def _one_para(text: str) -> Extraction:
    return Extraction(
        title="t",
        url="u",
        markdown="...",
        sections=[
            Section(locator="s1", paragraphs=[Paragraph(locator="s1-p1", text=text)])
        ],
    )


def test_korean_reading_time_uses_slower_rate():
    chars, minutes = estimate_reading(_one_para("가" * 1000))
    assert chars == 1000
    assert minutes == 2


def test_non_korean_reading_time_uses_faster_rate():
    chars, minutes = estimate_reading(_one_para("a" * 1000))
    assert chars == 1000
    assert minutes == 1


def test_very_short_article_still_reports_one_minute():
    _, minutes = estimate_reading(_one_para("짧다"))
    assert minutes == 1


# --- 인용 검증 ---------------------------------------------------------------


def _preview(quotes: list[dict]) -> Preview:
    return Preview.model_validate({**_VALID_PREVIEW, "quotes": quotes})


def test_invented_quote_is_dropped():
    """원문에 없는 문장은 모델이 지어낸 것이다. 버린다."""
    checked = verify_quotes(
        _preview([{"text": "저자는 사실 이렇게 말했다.", "locator": "s1-p1"}]), _ext()
    )
    assert checked.quotes == []


def test_real_quote_with_bad_locator_keeps_text_and_drops_position():
    """인용문 자체가 값어치다. 위치만 틀렸으면 텍스트는 살린다."""
    checked = verify_quotes(
        _preview([{"text": "요약은 원문을 평면화한다.", "locator": "s9-p9"}]), _ext()
    )
    assert len(checked.quotes) == 1
    assert checked.quotes[0].text == "요약은 원문을 평면화한다."
    assert checked.quotes[0].locator == ""
    assert checked.quotes[0].section is None


def test_valid_quote_gets_section_title_from_locator():
    """섹션 제목은 모델이 아니라 locator에서 끌어온다. 지어낼 여지를 없앤다."""
    checked = verify_quotes(
        _preview([{"text": "벤치마크를 돌렸다.", "locator": "s2-p1"}]), _ext()
    )
    assert checked.quotes[0].section == "성능 측정"


def test_quote_matching_ignores_whitespace_differences():
    """모델이 줄바꿈이나 공백을 다르게 옮겨도 같은 문장으로 본다."""
    checked = verify_quotes(
        _preview([{"text": "요약은   원문을\n평면화한다.", "locator": "s1-p1"}]), _ext()
    )
    assert len(checked.quotes) == 1


# --- make_preview ------------------------------------------------------------


async def test_make_preview_returns_validated_preview():
    result = await make_preview(_ext(), agent=FakeAgent(_VALID_PREVIEW))
    assert result.kind == "튜토리얼"
    assert result.claim_shape.startswith("스칼라 루프보다")
    assert result.quotes[0].section == "도입"


async def test_make_preview_rejects_response_missing_fields():
    with pytest.raises(PreviewError):
        await make_preview(_ext(), agent=FakeAgent({"about": "그것만 있다"}))


async def test_make_preview_strips_prompt_echo_from_prose_fields():
    """모델이 프롬프트 형식을 흉내내 붙인 태그·구분선을 저장 전에 걷어낸다."""
    leaked = {**_VALID_PREVIEW, "about": "<body>이 글은 SIMD를 다룬다.</body>"}
    result = await make_preview(_ext(), agent=FakeAgent(leaked))
    assert result.about == "이 글은 SIMD를 다룬다."


async def test_make_preview_keeps_quote_text_untouched_by_echo_stripping():
    """인용은 원문 그대로다. 원문에 그런 줄이 있었다면 지우는 쪽이 손해다."""
    ext = _one_para("=== 결론 === 이 줄은 원문에 있다.")
    response = {
        **_VALID_PREVIEW,
        "quotes": [{"text": "=== 결론 === 이 줄은 원문에 있다.", "locator": "s1-p1"}],
    }
    result = await make_preview(ext, agent=FakeAgent(response))
    assert result.quotes[0].text == "=== 결론 === 이 줄은 원문에 있다."
