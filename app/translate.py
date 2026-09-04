"""비한국어 원문을 문단 단위로 번역한다.

문단마다 따로 호출한다. 하나가 실패해도 나머지가 살고, 끝나는 대로 화면에 붙는다.
대신 문단 사이 문맥은 공유되지 않으므로 용어가 문단마다 조금씩 달라질 수 있다.

판정은 LLM 없이 글자 비율로 한다. 영어 용어가 섞인 한국어 글을 번역 대상으로
오인하지 않도록 문단 하나가 아니라 글 전체를 기준으로 본다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.models import Extraction
from app.reader import AgentRunner, strip_prompt_echo

# 한글 음절 U+AC00–U+D7A3. 자모(U+3131~)는 본문에 거의 안 나와 무시한다.
_HANGUL_START, _HANGUL_END = 0xAC00, 0xD7A3

# 코드 조각이나 숫자만 있는 글을 거르는 하한. CJK는 한 글자에 담기는 양이 많아
# 짧은 문장도 번역 대상이므로 낮게 잡는다.
_MIN_LETTERS = 10

# 한글이 이 비율 미만이면 비한국어 글로 본다.
KOREAN_THRESHOLD = 0.1

TRANSLATE_SCHEMA: dict = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _is_hangul(ch: str) -> bool:
    return _HANGUL_START <= ord(ch) <= _HANGUL_END


def korean_ratio(text: str) -> float:
    """글자(숫자·기호 제외) 중 한글 음절의 비율. 글자가 없으면 0."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if _is_hangul(ch)) / len(letters)


def document_text(extraction: Extraction) -> str:
    return "\n".join(
        p.text for s in extraction.sections for p in s.paragraphs
    )


def needs_translation(extraction: Extraction) -> bool:
    text = document_text(extraction)
    if sum(1 for ch in text if ch.isalpha()) < _MIN_LETTERS:
        return False
    return korean_ratio(text) < KOREAN_THRESHOLD


def build_translate_prompt(title: str, text: str) -> str:
    """입력을 XML 태그로 감싸 경계를 알린다.

    `=== 문단 시작 ===` 같은 구분선을 쓰면 모델이 그 형식을 대칭으로 흉내내
    출력까지 `=== 번역 시작 ===`으로 감싸버린다. 그게 text 필드에 그대로
    들어가 화면에 노출된다. XML 태그는 입력 구조를 나타내는 관례라 출력에
    따라붙지 않고, 태그가 무슨 역할인지 설명해두면 더 안 섞인다.
    """
    return (
        "아래 문단을 한국어로 번역하라.\n\n"
        "입력은 XML 태그로 구분되어 있다. 태그의 역할:\n"
        "- <document_title>: 문단이 속한 글의 제목. 맥락 파악용이며 번역 대상이 아니다.\n"
        "- <paragraph>: 번역할 원문. 이 태그 안의 내용만 번역한다.\n\n"
        "번역 규칙:\n"
        "- 원문의 뜻과 어조를 그대로 옮긴다. 요약하거나 설명을 덧붙이지 않는다.\n"
        "- 고유명사와 기술 용어는 널리 쓰이는 한국어 표기가 있으면 그것을 쓰고, "
        "없으면 원어를 그대로 둔다.\n\n"
        "출력 규칙:\n"
        "- text 필드에는 번역문 본문만 담는다.\n"
        "- 위의 XML 태그를 출력에 다시 적지 마라. 구분선, 머리말, 원문도 넣지 마라.\n\n"
        f"<document_title>{title}</document_title>\n\n"
        f"<paragraph>\n{text}\n</paragraph>"
    )


async def translate_extraction(
    extraction: Extraction,
    *,
    agent: AgentRunner,
    concurrency: int = 6,
    on_paragraph: Callable[[str, str], Awaitable[None]] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """문단별로 번역한다. (locator→번역문, 실패한 locator 목록)을 돌려준다.

    on_paragraph는 문단 하나가 끝날 때마다 불린다. 호출자가 그때그때 저장하면
    페이지가 위에서부터 채워진다.
    """
    targets = [
        p for s in extraction.sections for p in s.paragraphs if p.text.strip()
    ]
    gate = asyncio.Semaphore(concurrency)
    done: dict[str, str] = {}
    failed: list[str] = []

    async def translate_one(locator: str, text: str) -> None:
        async with gate:
            try:
                raw = await agent.run(
                    build_translate_prompt(extraction.title, text), TRANSLATE_SCHEMA
                )
                translated = strip_prompt_echo(str(raw.get("text", "")))
            except Exception:
                # 한 문단이 실패해도 나머지는 계속 간다.
                failed.append(locator)
                return
            if not translated:
                failed.append(locator)
                return
            done[locator] = translated
            if on_paragraph is not None:
                await on_paragraph(locator, translated)

    await asyncio.gather(*(translate_one(p.locator, p.text) for p in targets))
    failed.sort()
    return done, failed
