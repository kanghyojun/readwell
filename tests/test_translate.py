"""언어 판정과 문단 단위 번역."""

from __future__ import annotations

import asyncio

import pytest

from app.models import Extraction, Paragraph, Section
from app.translate import (
    build_translate_prompt,
    korean_ratio,
    needs_translation,
    translate_extraction,
)


def _ext(*texts: str) -> Extraction:
    return Extraction(
        title="t",
        url="https://x/y",
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


# --- 언어 판정 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("요약은 원문을 평면화한다.", False),
        ("Summarization flattens the source text.", True),
        ("要約は原文を平坦化する。", True),
        ("Полный текст важнее конспекта.", True),
        # 영어 용어가 섞여도 한국어 글이다
        ("이 글은 LLM의 context window를 다룬다. RAG와 비교한다.", False),
        # 코드나 숫자만 있는 문단은 번역할 게 없다
        ("x = 1 + 2", False),
        ("", False),
    ],
)
def test_needs_translation(text: str, expected: bool) -> None:
    assert needs_translation(_ext(text)) is expected


def test_korean_ratio_ignores_punctuation_and_spaces() -> None:
    assert korean_ratio("한글!!! ...") == 1.0
    assert korean_ratio("abc") == 0.0


def test_needs_translation_uses_whole_document() -> None:
    """앞 문단이 영어 인용이어도 글 전체가 한국어면 번역하지 않는다."""
    ext = _ext(
        '"Premature optimization is the root of all evil."',
        "커누스의 이 문장은 자주 잘못 인용된다.",
        "원문 맥락에서는 측정 없는 최적화를 경계하라는 뜻이다.",
    )
    assert needs_translation(ext) is False


# --- 프롬프트 ----------------------------------------------------------------


def test_prompt_wraps_input_in_xml_tags() -> None:
    prompt = build_translate_prompt("제목", "Hello there.")
    assert "<paragraph>\nHello there.\n</paragraph>" in prompt
    assert "<document_title>제목</document_title>" in prompt


def test_prompt_explains_what_each_tag_is_for() -> None:
    """태그가 무슨 역할인지 알려줘야 모델이 제목까지 번역하지 않는다."""
    prompt = build_translate_prompt("제목", "text")
    assert "<document_title>:" in prompt
    assert "<paragraph>:" in prompt


def test_prompt_has_no_symmetric_delimiters() -> None:
    """`=== 문단 시작 ===` 같은 구분선을 쓰면 모델이 출력도 그렇게 감싼다.

    실제로 `=== 번역 시작 === ... === 번역 끝 ===`이 번역문에 섞여 나왔다.
    프롬프트에 없던 단어까지 지어내 대칭을 맞춘다.
    """
    prompt = build_translate_prompt("제목", "text")
    assert "===" not in prompt


def test_prompt_forbids_echoing_the_tags() -> None:
    prompt = build_translate_prompt("제목", "text")
    assert "출력에 다시 적지 마라" in prompt


# --- 번역 실행 ---------------------------------------------------------------


class _Agent:
    """문단 텍스트 앞에 표식을 붙여 돌려주는 가짜 번역기."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, prompt: str, schema: dict) -> dict:
        self.calls.append(prompt)
        await asyncio.sleep(0)
        return {"text": "[번역]"}


@pytest.mark.asyncio
async def test_translates_every_paragraph():
    agent = _Agent()
    ext = _ext("one", "two", "three")
    done, failed = await translate_extraction(ext, agent=agent)
    assert done == {"s1-p1": "[번역]", "s1-p2": "[번역]", "s1-p3": "[번역]"}
    assert failed == []
    assert len(agent.calls) == 3


@pytest.mark.asyncio
async def test_reports_progress_as_each_paragraph_lands():
    """문단 하나가 끝날 때마다 알려야 화면에 점진적으로 붙는다."""
    seen: list[str] = []

    async def on_paragraph(locator: str, text: str) -> None:
        seen.append(locator)

    await translate_extraction(
        _ext("one", "two"), agent=_Agent(), on_paragraph=on_paragraph
    )
    assert sorted(seen) == ["s1-p1", "s1-p2"]


@pytest.mark.asyncio
async def test_one_failed_paragraph_does_not_stop_the_rest():
    class Flaky:
        async def run(self, prompt: str, schema: dict) -> dict:
            if "two" in prompt:
                raise RuntimeError("모델 오류")
            return {"text": "[번역]"}

    done, failed = await translate_extraction(_ext("one", "two", "three"), agent=Flaky())
    assert set(done) == {"s1-p1", "s1-p3"}
    assert failed == ["s1-p2"]


@pytest.mark.asyncio
async def test_concurrency_is_capped():
    """문단마다 호출하므로 동시 실행 수를 제한해야 한다."""
    live = 0
    peak = 0

    class Counting:
        async def run(self, prompt: str, schema: dict) -> dict:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return {"text": "[번역]"}

    ext = _ext(*[f"p{i}" for i in range(20)])
    await translate_extraction(ext, agent=Counting(), concurrency=3)
    assert peak <= 3


@pytest.mark.asyncio
async def test_blank_paragraphs_are_skipped():
    agent = _Agent()
    done, failed = await translate_extraction(_ext("one", "   ", "two"), agent=agent)
    assert set(done) == {"s1-p1", "s1-p3"}
    assert len(agent.calls) == 2
