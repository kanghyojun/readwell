"""preview: 읽을지 말지 판단할 재료를 만든다.

reader.py와 목적이 반대다. reader는 읽기로 이미 정한 사람을 돕고, preview는 아직
정하지 않은 사람을 돕는다. 그래서 원칙도 반대로 선다. **내용을 주지 않고 성격을 준다.**
결론까지 알려주면 원문을 읽을 이유가 사라져서, 판단을 돕는 게 아니라 판단을 없앤다.

문헌 요약 용어로는 informative가 아니라 indicative 요약이다. 원문을 대체하는 게 아니라
원문을 가리킨다. 자세한 배경은 docs/2026-09-04-readwell-preview-design.md 참고.
"""

from __future__ import annotations

import math
import re

from pydantic import ValidationError

from app.models import Extraction, Preview, PreviewQuote
from app.reader import AgentRunner, strip_prompt_echo
from app.translate import KOREAN_THRESHOLD, document_text, korean_ratio

# 이 길이를 넘으면 전문 대신 골격만 넣는다. Config가 덮어쓴다.
PREVIEW_MAX_CHARS = 40000

# 분당 읽는 글자 수. 한글은 한 글자에 담기는 양이 많아 느리게 잡는다.
_KOREAN_CPM = 500
_OTHER_CPM = 1000

# 모델이 자유롭게 서술하는 필드. 프롬프트 형식이 새어들 여지가 여기에만 있다.
_PROSE_FIELDS = ("about", "kind", "claim_shape", "evidence", "audience", "not_covered")

_WS_RE = re.compile(r"\s+")

PREVIEW_SYSTEM_PROMPT = (
    "너는 사람이 글을 읽을지 말지 판단하도록 돕는 안내자다. 요약기가 아니다. "
    "규칙: (1) 글이 내놓는 결론, 수치, 해법을 옮기지 않는다. 그런 게 있다는 사실만 "
    "알린다. (2) 인용은 원문 문장을 한 글자도 바꾸지 않고 그대로 옮기고, 제공된 위치 "
    "식별자 locator(예: s2-p3)를 붙인다. (3) 본문에 없는 내용을 지어내지 않는다. "
    "locator는 프롬프트에 대괄호로 표시된 것만 쓴다. (4) 인용을 뺀 모든 서술은 "
    "한국어로 쓴다. 원문이 무슨 언어든 마찬가지다."
)


class PreviewError(RuntimeError):
    """프리뷰 스키마 검증 실패 등 preview 오류."""


# --- 프롬프트 구성 -----------------------------------------------------------


def render_skeleton(extraction: Extraction) -> str:
    """섹션 제목 전부와 섹션마다 첫 문단만. 긴 글을 프롬프트에 넣을 때 쓴다.

    대부분의 글은 앞머리에 논지를 세운다. 판단 재료로는 골격으로 충분하다.
    대신 인용이 각 섹션 앞부분에서만 나온다.
    """
    out: list[str] = []
    for s in extraction.sections:
        if s.title:
            out.append(f"## {s.title}  ({s.locator})")
        if s.paragraphs:
            p = s.paragraphs[0]
            out.append(f"[{p.locator}] {p.text}")
    return "\n\n".join(out)


def render_full(extraction: Extraction) -> str:
    """문단마다 [locator]를 앞에 붙인 본문 전체."""
    out: list[str] = []
    for s in extraction.sections:
        if s.title:
            out.append(f"## {s.title}  ({s.locator})")
        for p in s.paragraphs:
            out.append(f"[{p.locator}] {p.text}")
    return "\n\n".join(out)


def build_preview_prompt(
    extraction: Extraction, *, max_chars: int = PREVIEW_MAX_CHARS
) -> str:
    body = (
        render_skeleton(extraction)
        if len(document_text(extraction)) > max_chars
        else render_full(extraction)
    )
    return (
        "아래 글을 읽을지 말지 판단할 재료를 스키마에 맞는 JSON으로 답하라.\n\n"
        "읽는 사람은 아직 이 글을 읽을지 정하지 않았다. 네 일은 이 글이 어떤 물건인지 "
        "알려주는 것이지, 이 글이 내놓는 답을 알려주는 게 아니다. 결론과 수치와 해법은 "
        "옮기지 말고 그런 게 있다는 사실만 알려라. 결론까지 알려주면 원문을 읽을 이유가 "
        "사라진다.\n\n"
        "입력은 XML 태그로 구분되어 있다. 태그의 역할:\n"
        "- <title>: 글 제목.\n"
        "- <source_url>: 원문 주소.\n"
        "- <body>: 판단할 본문. 문단마다 [locator]가 대괄호로 앞에 붙어 있다.\n\n"
        "채울 필드:\n"
        "- about: 무엇에 관한 글인지 2~3문장. 주제와 범위만 적고 결론은 넣지 마라.\n"
        "- kind: 글의 종류 한 마디. 튜토리얼 / 주장글 / 경험담 / 레퍼런스 / 뉴스 / "
        "리뷰 중 가까운 것.\n"
        "- claimShape: 어떤 종류의 주장을 하는지. 'X보다 Y가 낫다고 주장한다'처럼 "
        "주장의 모양만 적고 어느 쪽이 이겼는지는 적지 마라.\n"
        "- evidence: 근거가 무엇인지. 벤치마크 / 저자 경험 / 논문 인용 / 수학적 증명 / "
        "없음 중에서.\n"
        "- audience: 누구를 위한 글이고 무엇을 안다고 전제하는지.\n"
        "- notCovered: 제목이나 주제를 보고 기대할 법하지만 이 글이 다루지 않는 것.\n"
        "- quotes: 저자의 목소리가 가장 잘 드러난 원문 문장 2~3개. 한 글자도 바꾸지 말고 "
        "그대로 옮기고 locator를 붙여라. 요약하거나 다듬지 마라.\n\n"
        "모든 locator는 <body>에 대괄호로 표시된 값만 쓴다.\n"
        "필드 값에는 내용만 담는다. 위의 XML 태그를 값 안에 다시 적지 마라.\n"
        f"<title>{extraction.title}</title>\n"
        f"<source_url>{extraction.url}</source_url>\n\n"
        f"<body>\n{body}\n</body>"
    )


# --- 분량과 읽기 시간 --------------------------------------------------------


def estimate_reading(extraction: Extraction) -> tuple[int, int]:
    """(글자 수, 예상 읽기 분). 세지 않아도 아는 값이라 LLM에 맡기지 않는다."""
    text = document_text(extraction)
    cpm = _KOREAN_CPM if korean_ratio(text) >= KOREAN_THRESHOLD else _OTHER_CPM
    return len(text), max(1, math.ceil(len(text) / cpm))


# --- 인용 검증 ---------------------------------------------------------------


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def verify_quotes(preview: Preview, extraction: Extraction) -> Preview:
    """지어낸 인용은 버리고, 위치만 틀린 인용은 텍스트를 살린다.

    인용문 자체가 판단 재료다. 위치는 승격 후 원문으로 돌아가는 앵커일 뿐이라
    틀렸으면 위치만 비운다. 반대로 원문에 없는 문장은 통째로 버린다. LLM을 안 거친
    날것이라는 게 인용의 값어치인데, 지어낸 문장은 그 전제를 깬다.
    """
    haystack = _normalize(document_text(extraction))
    titles = {s.locator: s.title for s in extraction.sections}
    valid = extraction.locators()

    kept: list[PreviewQuote] = []
    for q in preview.quotes:
        text = _normalize(q.text)
        if not text or text not in haystack:
            continue
        if q.locator in valid:
            kept.append(
                PreviewQuote(
                    text=text,
                    locator=q.locator,
                    section=titles.get(q.locator.split("-")[0]),
                )
            )
        else:
            kept.append(PreviewQuote(text=text, locator="", section=None))
    return preview.model_copy(update={"quotes": kept})


# --- 실행 --------------------------------------------------------------------


async def make_preview(
    extraction: Extraction,
    *,
    agent: AgentRunner,
    max_chars: int = PREVIEW_MAX_CHARS,
) -> Preview:
    prompt = build_preview_prompt(extraction, max_chars=max_chars)
    raw = await agent.run(prompt, Preview.json_schema())
    try:
        parsed = Preview.model_validate(raw)
    except ValidationError as e:
        raise PreviewError(f"프리뷰 스키마 검증 실패: {e}") from e

    # 자유 서술 필드에만 건다. quotes는 원문 인용이라 건드리면 손해다.
    cleaned = {name: strip_prompt_echo(getattr(parsed, name)) for name in _PROSE_FIELDS}
    return verify_quotes(parsed.model_copy(update=cleaned), extraction)
