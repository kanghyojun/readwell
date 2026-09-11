"""분석/추출 스키마 모델 테스트."""

from app.models import Analysis, Extraction, Paragraph, Section


def _sample_extraction() -> Extraction:
    return Extraction(
        title="테스트 글",
        url="https://example.com/post",
        markdown="# 테스트 글\n\n본문",
        sections=[
            Section(
                locator="s1",
                title="들어가며",
                paragraphs=[
                    Paragraph(locator="s1-p1", text="첫 문단"),
                    Paragraph(locator="s1-p2", text="둘째 문단"),
                ],
            ),
            Section(
                locator="s2",
                title="본론",
                paragraphs=[Paragraph(locator="s2-p1", text="본론 문단")],
            ),
        ],
    )


def test_extraction_collects_all_paragraph_locators():
    ext = _sample_extraction()
    assert ext.locators() == {"s1-p1", "s1-p2", "s2-p1"}


def test_analysis_parses_spec_camelcase_shape():
    data = {
        "scan": "이 글은 무엇에 관한지 한 문단",
        "gist": [{"section": "본론", "oneLine": "한 줄 요지", "locator": "s2-p1"}],
        "claims": [
            {
                "claim": "핵심 주장",
                "evidence": "근거",
                "locator": "s2-p1",
                "weakness": "약한 지점",
            }
        ],
        "questions": [
            {"q": "핵심 주장은?", "answerQuote": "원문 문장 그대로", "locator": "s1-p2"}
        ],
        "critique": [
            {
                "hiddenPremise": "숨은 전제",
                "weakEvidence": "약한 근거",
                "missingCounterexample": "빠진 반례",
                "locator": "s2-p1",
            }
        ],
    }
    a = Analysis.model_validate(data)
    assert a.gist[0].one_line == "한 줄 요지"
    assert a.questions[0].answer_quote == "원문 문장 그대로"
    assert a.critique[0].hidden_premise == "숨은 전제"
    assert a.claims[0].weakness == "약한 지점"


def test_claim_weakness_is_optional():
    a = Analysis.model_validate(
        {
            "scan": "s",
            "gist": [],
            "claims": [{"claim": "c", "evidence": "e", "locator": "s1-p1"}],
            "questions": [],
            "critique": [],
        }
    )
    assert a.claims[0].weakness is None


def test_analysis_roundtrips_to_camelcase_json():
    a = Analysis.model_validate(
        {
            "scan": "s",
            "gist": [{"section": "본론", "oneLine": "요지", "locator": "s2-p1"}],
            "claims": [],
            "questions": [],
            "critique": [],
        }
    )
    dumped = a.model_dump(by_alias=True)
    assert dumped["gist"][0]["oneLine"] == "요지"
    assert "one_line" not in dumped["gist"][0]


def test_invalid_locators_detected():
    ext = _sample_extraction()
    a = Analysis.model_validate(
        {
            "scan": "s",
            "gist": [
                {"section": "본론", "oneLine": "요지", "locator": "s2-p1"},
                {"section": "없음", "oneLine": "허구", "locator": "s9-p9"},
            ],
            "claims": [],
            "questions": [{"q": "?", "answerQuote": "a", "locator": "s1-p1"}],
            "critique": [],
        }
    )
    assert a.invalid_locators(ext.locators()) == {"s9-p9"}


def test_valid_analysis_has_no_invalid_locators():
    ext = _sample_extraction()
    a = Analysis.model_validate(
        {
            "scan": "s",
            "gist": [{"section": "본론", "oneLine": "요지", "locator": "s2-p1"}],
            "claims": [{"claim": "c", "evidence": "e", "locator": "s1-p2"}],
            "questions": [],
            "critique": [],
        }
    )
    assert a.invalid_locators(ext.locators()) == set()


def test_analysis_json_schema_uses_aliases():
    schema = Analysis.json_schema()
    # 최상위 필드
    assert set(schema["properties"].keys()) >= {
        "scan",
        "gist",
        "claims",
        "questions",
        "critique",
    }
    # 스키마는 문자열화 가능한 표준 JSON Schema여야 함
    import json

    json.dumps(schema)


def test_analysis_json_schema_requires_every_section():
    """scan만 채워도 통과하면 모델이 가끔 나머지를 비운 채 끝낸다. 실제로 겪었다."""
    schema = Analysis.json_schema()
    lists = ["gist", "claims", "questions", "critique"]
    assert set(schema["required"]) == {"scan", *lists}
    for name in lists:
        assert schema["properties"][name]["minItems"] == 1
