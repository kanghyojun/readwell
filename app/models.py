"""readwell 데이터 모델.

- Extraction 계열: 원문 추출 결과. 문단마다 locator(예: ``s2-p3``)를 단다.
- Analysis 계열: reader가 내는 분석. 스펙 JSON(camelCase)과 그대로 맞물린다.

코드에서는 snake_case로 접근하고, JSON 입출력은 camelCase alias로 스펙과 일치시킨다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class Session(_CamelModel):
    id: str
    url: str
    title: str
    created_at: str
    extraction: Extraction
    analysis: Analysis | None = None
    conversation: list[ChatTurn] = Field(default_factory=list)
