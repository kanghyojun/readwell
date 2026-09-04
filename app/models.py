"""readwell 데이터 모델.

- Extraction 계열: 원문 추출 결과. 문단마다 locator(예: ``s2-p3``)를 단다.
- Analysis 계열: reader가 내는 분석. 스펙 JSON(camelCase)과 그대로 맞물린다.

코드에서는 snake_case로 접근하고, JSON 입출력은 camelCase alias로 스펙과 일치시킨다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 읽기 작업의 진행 상태. extracting/analyzing이면 아직 백그라운드에서 도는 중이다.
SessionStatus = Literal["extracting", "analyzing", "done", "failed"]
PENDING_STATUSES: frozenset[str] = frozenset({"extracting", "analyzing"})

# 프리뷰 작업의 진행 상태. 세션과 단계 이름이 달라 따로 둔다.
PreviewStatus = Literal["extracting", "previewing", "done", "failed"]
PREVIEW_PENDING: frozenset[str] = frozenset({"extracting", "previewing"})


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# --- 추출 결과 ---------------------------------------------------------------


class Paragraph(_CamelModel):
    locator: str
    text: str


class Section(_CamelModel):
    locator: str
    title: str | None = None
    paragraphs: list[Paragraph] = Field(default_factory=list)


class Extraction(_CamelModel):
    title: str
    url: str
    markdown: str
    sections: list[Section] = Field(default_factory=list)

    def locators(self) -> set[str]:
        """원문에 실재하는 모든 문단 locator."""
        return {p.locator for s in self.sections for p in s.paragraphs}


# --- 분석 결과 ---------------------------------------------------------------


class GistItem(_CamelModel):
    section: str
    one_line: str = Field(alias="oneLine")
    locator: str


class Claim(_CamelModel):
    claim: str
    evidence: str
    locator: str
    weakness: str | None = None


class QuestionAnswer(_CamelModel):
    q: str
    answer_quote: str = Field(alias="answerQuote")
    locator: str


class CritiqueItem(_CamelModel):
    hidden_premise: str = Field(alias="hiddenPremise")
    weak_evidence: str = Field(alias="weakEvidence")
    missing_counterexample: str = Field(alias="missingCounterexample")
    locator: str


class Analysis(_CamelModel):
    scan: str
    gist: list[GistItem] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    questions: list[QuestionAnswer] = Field(default_factory=list)
    critique: list[CritiqueItem] = Field(default_factory=list)

    def all_locators(self) -> set[str]:
        items = [*self.gist, *self.claims, *self.questions, *self.critique]
        return {item.locator for item in items}

    def invalid_locators(self, valid: set[str]) -> set[str]:
        """원문에 없는 위치를 가리키는 locator들."""
        return {loc for loc in self.all_locators() if loc not in valid}

    @classmethod
    def json_schema(cls) -> dict:
        """agent output_format(json_schema)에 넘길 표준 JSON Schema(camelCase)."""
        return cls.model_json_schema(by_alias=True)


# --- 세션(원문 + 분석 + 대화) ------------------------------------------------


class ChatTurn(_CamelModel):
    """추가 질문 한 번의 왕복. answer는 원문 인용 기반."""

    question: str
    answer: str
    locators: list[str] = Field(default_factory=list)
    kind: str = "ask"  # ask | feynman | gloss


class Translation(_CamelModel):
    """비한국어 원문의 문단별 번역. 문단마다 따로 채워지므로 부분 상태가 정상이다."""

    done: bool = False
    paragraphs: dict[str, str] = Field(default_factory=dict)
    note: str | None = None  # 건너뛴 사유나 부분 실패 안내

    def missing(self, locators: set[str]) -> set[str]:
        return locators - set(self.paragraphs)


class Session(_CamelModel):
    """읽기 한 건. 추출·분석이 끝나기 전에도 먼저 만들어 두고 상태를 갱신한다.

    기본값이 ``done``이라 status 필드가 없던 예전 세션 파일도 그대로 읽힌다.
    """

    id: str
    url: str
    title: str
    created_at: str
    status: SessionStatus = "done"
    error: str | None = None
    questions: list[str] = Field(default_factory=list)  # 읽기 전에 정한 질문. 비면 프리셋
    extraction: Extraction | None = None
    analysis: Analysis | None = None
    translation: Translation | None = None  # 한국어 원문이면 None
    conversation: list[ChatTurn] = Field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.status in PENDING_STATUSES


# --- 프리뷰(읽기 전 판단용) --------------------------------------------------


class PreviewQuote(_CamelModel):
    """저자의 목소리가 드러난 원문 문장. 판단 재료 중 유일하게 LLM을 안 거친 것."""

    text: str
    locator: str
    # 섹션 제목은 모델이 아니라 locator에서 끌어온다. 지어낼 여지를 없앤다.
    section: str | None = None


class Preview(_CamelModel):
    """읽을지 말지 정하는 데 쓰는 성격 카드.

    내용이 아니라 성격을 담는다. 결론을 담으면 원문을 읽을 이유가 사라져서,
    판단을 돕는 게 아니라 판단을 없앤다.
    """

    about: str  # 무엇에 관한 글인가. 주제와 범위
    kind: str  # 튜토리얼 / 주장글 / 경험담 / 레퍼런스 / 뉴스
    claim_shape: str = Field(alias="claimShape")  # 주장의 모양. 결론 자체가 아니다
    evidence: str  # 벤치마크 / 저자 경험 / 인용 / 없음
    audience: str  # 대상 독자와 전제하는 배경지식
    not_covered: str = Field(alias="notCovered")  # 이 글이 다루지 않는 것
    quotes: list[PreviewQuote] = Field(default_factory=list)

    @classmethod
    def json_schema(cls) -> dict:
        """agent output_format(json_schema)에 넘길 표준 JSON Schema(camelCase)."""
        return cls.model_json_schema(by_alias=True)


class PreviewSession(_CamelModel):
    """훑어보기 한 건. 읽기로 결정하면 promoted_to에 승격된 세션 id가 남는다.

    세션과 달리 목록에 오르지 않고 TTL이 지나면 지워진다. 판단하고 버릴 물건이다.
    """

    id: str
    url: str
    title: str
    created_at: str
    status: PreviewStatus = "extracting"
    error: str | None = None
    extraction: Extraction | None = None
    preview: Preview | None = None
    # 분량은 세지 않아도 알 수 있다. LLM에 맡기면 틀리게 센다.
    char_count: int = 0
    read_minutes: int = 0
    promoted_to: str | None = None

    @property
    def pending(self) -> bool:
        return self.status in PREVIEW_PENDING
