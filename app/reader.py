"""reader: claude-agent-sdk로 읽기 방법론을 실행한다.

원칙: reader는 요약기가 아니라 안내자·검증자다. 모든 산출물에 원문 인용과 locator를
붙이고, agent가 지어낸 위치가 아니라 제공된 [sX-pY]만 쓰게 강제한다. 결과는 스키마
검증을 통과해야 하며, locator가 실재 위치를 가리키는지 별도로 확인한다.

agent 호출은 AgentRunner 프로토콜로 추상화한다. 테스트는 가짜 러너로, 실제는
ClaudeAgentRunner(구독 OAuth)로 돈다.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.models import Analysis, ChatTurn, Extraction, Session

# 미결 2번 결정: 방법론 "질문 기반 추출" 기반 프리셋 5개.
PRESET_QUESTIONS: list[str] = [
    "이 글의 핵심 주장은 무엇인가?",
    "그 주장을 뒷받침하는 가장 강한 근거는?",
    "이 글이 기존 통념이나 대안과 다른 지점은?",
    "독자가 실제로 취할 수 있는 행동이나 결론은?",
    "저자가 스스로 인정하는 한계나 조건은?",
]

SYSTEM_PROMPT = (
    "너는 긴 글을 대신 요약하는 도구가 아니라, 사람이 원문을 빠르고 정확하게 읽도록 "
    "돕는 안내자이자 검증자다. 규칙: (1) 모든 항목에는 원문 문장을 그대로 인용하거나 "
    "짚고, 반드시 제공된 위치 식별자 locator(예: s2-p3)를 붙인다. (2) 본문에 없는 "
    "내용을 지어내지 않는다. (3) locator는 프롬프트에 대괄호로 표시된 것만 쓴다. "
    "존재하지 않는 위치를 만들지 않는다. (4) 답을 네 말로 풀어 요약하지 말고 원문에 "
    "근거를 둔다."
)


class ReaderError(RuntimeError):
    """분석 스키마 검증 실패 등 reader 오류."""


class AgentRunner(Protocol):
    """프롬프트 + JSON 스키마 → 구조화 출력(dict)."""

    async def run(self, prompt: str, schema: dict) -> dict: ...


# --- 프롬프트 구성 -----------------------------------------------------------


def render_located_body(extraction: Extraction) -> str:
    """문단마다 [locator]를 앞에 붙인 본문. agent가 정확한 위치를 쓰게 하는 근거."""
    out: list[str] = []
    for s in extraction.sections:
        if s.title:
            out.append(f"## {s.title}  ({s.locator})")
        for p in s.paragraphs:
            out.append(f"[{p.locator}] {p.text}")
    return "\n\n".join(out)


def build_analysis_prompt(extraction: Extraction) -> str:
    questions = "\n".join(f"  {i}. {q}" for i, q in enumerate(PRESET_QUESTIONS, 1))
    return (
        f"제목: {extraction.title}\n"
        f"원문 URL: {extraction.url}\n\n"
        "아래 본문을 방법론대로 분석해 스키마에 맞는 JSON으로 답하라.\n"
        "- scan: 이 글이 전체적으로 무엇에 관한지 한 문단.\n"
        "- gist: 섹션별 한 줄 요지 + locator(원문 회귀용 지도).\n"
        "- claims: 핵심 주장, 근거, locator, 약한 지점(있으면).\n"
        "- questions: 아래 프리셋 질문의 답을 원문 문장 인용(answerQuote)과 locator로. "
        "답이 없으면 answerQuote를 '없음'으로.\n"
        f"{questions}\n"
        "- critique: 주장별 숨은 전제, 약한 근거, 빠진 반례 + locator.\n\n"
        "모든 locator는 아래 본문에 대괄호로 표시된 값만 쓴다.\n\n"
        "=== 본문 시작 ===\n"
        f"{render_located_body(extraction)}\n"
        "=== 본문 끝 ==="
    )


_ASK_KIND_INSTRUCTIONS = {
    "ask": "질문에 대해 원문에 근거해 답하라. 답이 담긴 원문 위치(locator)를 함께 대라.",
    "feynman": (
        "사용자가 자기 이해를 적었다. 새 정보를 길게 덧붙이지 말고, 틀리게 이해한 부분, "
        "빠뜨린 핵심, 근거 없이 단정한 부분만 원문 locator를 근거로 짚어라."
    ),
    "gloss": (
        "사용자가 막히는 용어나 구절을 물었다. 그 용어·구절만 원문 맥락에서 간단히 풀되, "
        "관련 원문 위치(locator)를 대라. 글 전체를 요약하지 마라."
    ),
}

ASK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "locators": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "locators"],
}


def build_ask_prompt(session: Session, question: str, kind: str = "ask") -> str:
    instruction = _ASK_KIND_INSTRUCTIONS.get(kind, _ASK_KIND_INSTRUCTIONS["ask"])
    parts = [
        f"제목: {session.title}",
        f"원문 URL: {session.url}",
        "",
        instruction,
    ]
    if session.conversation:
        parts += ["", "이전 대화:"]
        for turn in session.conversation:
            parts.append(f"- Q: {turn.question}")
            parts.append(f"  A: {turn.answer}")
    parts += [
        "",
        f"사용자 입력: {question}",
        "",
        "=== 원문 시작 ===",
        render_located_body(session.extraction),
        "=== 원문 끝 ===",
    ]
    return "\n".join(parts)


# --- 실행 --------------------------------------------------------------------


async def analyze(extraction: Extraction, *, agent: AgentRunner) -> Analysis:
    prompt = build_analysis_prompt(extraction)
    raw = await agent.run(prompt, Analysis.json_schema())
    try:
        return Analysis.model_validate(raw)
    except ValidationError as e:
        raise ReaderError(f"분석 스키마 검증 실패: {e}") from e


async def ask(
    session: Session, question: str, *, agent: AgentRunner, kind: str = "ask"
) -> ChatTurn:
    prompt = build_ask_prompt(session, question, kind)
    raw = await agent.run(prompt, ASK_SCHEMA)
    return ChatTurn(
        question=question,
        answer=str(raw.get("answer", "")),
        locators=list(raw.get("locators", [])),
        kind=kind,
    )


def check_locators(analysis: Analysis, extraction: Extraction) -> set[str]:
    """분석의 locator 중 원문에 실재하지 않는 것들."""
    return analysis.invalid_locators(extraction.locators())


# --- 실제 agent 러너 (구독 OAuth) -------------------------------------------


class ClaudeAgentRunner:
    """claude-agent-sdk로 구조화 출력을 받아오는 실제 러너.

    구독 OAuth(~/.claude/.credentials.json)를 SDK가 자동으로 쓴다. 개인용 전제.
    """

    def __init__(self, model: str | None = None):
        self.model = model

    async def run(self, prompt: str, schema: dict) -> dict:
        # 지연 임포트: 테스트/추출만 쓸 때 SDK·CLI 부재로 실패하지 않도록.
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            output_format={"type": "json_schema", "schema": schema},
            allowed_tools=[],
            setting_sources=[],  # SDK 격리: 프로젝트 설정/CLAUDE.md 로드 안 함
            max_turns=1,
            model=self.model,
        )
        structured: dict | None = None
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    raise ReaderError(f"agent 오류: {msg.errors or msg.result}")
                structured = msg.structured_output
        if structured is None:
            raise ReaderError("agent가 structured_output을 반환하지 않음")
        return structured
