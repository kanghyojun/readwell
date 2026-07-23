"""extractor 테스트: 마크다운 분절(locator), trafilatura 추출, Jina 폴백."""

from pathlib import Path

import httpx
import respx

from app.extractor import extract, jina_markdown, markdown_from_html, sectionize

FIXTURES = Path(__file__).parent / "fixtures"


# --- sectionize (순수 로직) --------------------------------------------------


def test_sectionize_splits_by_headers_and_assigns_locators():
    md = "# 제목\n\n첫 문단.\n\n둘째 문단.\n\n## 다음 섹션\n\n셋째 문단."
    secs = sectionize(md)
    assert secs[0].title == "제목"
    assert [p.locator for p in secs[0].paragraphs] == ["s1-p1", "s1-p2"]
    assert secs[1].title == "다음 섹션"
    assert secs[1].paragraphs[0].locator == "s2-p1"
    assert secs[1].paragraphs[0].text == "셋째 문단."


def test_sectionize_no_headers_puts_all_in_s1():
    md = "문단 A.\n\n문단 B."
    secs = sectionize(md)
    assert len(secs) == 1
    assert secs[0].locator == "s1"
    assert secs[0].title is None
    assert [p.locator for p in secs[0].paragraphs] == ["s1-p1", "s1-p2"]


def test_sectionize_preamble_before_first_header():
    md = "서두 문단.\n\n# 첫 헤더\n\n본문 문단."
    secs = sectionize(md)
    assert secs[0].title is None
    assert secs[0].paragraphs[0].text == "서두 문단."
    assert secs[1].title == "첫 헤더"
    assert secs[1].paragraphs[0].locator == "s2-p1"


def test_sectionize_ignores_blank_lines():
    md = "# H\n\n\n\n문단.\n\n\n"
    secs = sectionize(md)
    assert len(secs[0].paragraphs) == 1
    assert secs[0].paragraphs[0].text == "문단."


# --- trafilatura 추출 (HTML 픽스처) -----------------------------------------


def test_markdown_from_html_extracts_title_and_body():
    html = (FIXTURES / "sample_blog.html").read_text(encoding="utf-8")
    result = markdown_from_html(html)
    assert result is not None
    title, md = result
    assert title == "긴 글 잘 읽기 실험"
    assert "평면화" in md
    assert "방법" in md
    # 네비게이션/푸터는 빠져야 한다
    assert "모든 권리 보유" not in md


def test_markdown_from_html_returns_none_for_empty():
    assert markdown_from_html("<html><body></body></html>") is None


# --- Jina 폴백 ---------------------------------------------------------------


@respx.mock
def test_jina_markdown_fetches_reader_endpoint():
    respx.get("https://r.jina.ai/https://example.com/post").mock(
        return_value=httpx.Response(200, text="# 제목\n\n본문 문단.")
    )
    title, md = jina_markdown("https://example.com/post")
    assert "본문 문단." in md


@respx.mock
def test_jina_markdown_parses_preamble():
    text = (
        "Title: 실제 제목\n"
        "URL Source: https://example.com/post\n\n"
        "Markdown Content:\n"
        "# 본문 헤더\n\n문단 하나."
    )
    respx.get("https://r.jina.ai/https://example.com/post").mock(
        return_value=httpx.Response(200, text=text)
    )
    title, md = jina_markdown("https://example.com/post")
    assert title == "실제 제목"
    assert md.startswith("# 본문 헤더")


# --- extract 오케스트레이션 (의존성 주입) ------------------------------------


def test_extract_uses_local_when_available():
    def local(url):
        return ("로컬 제목", "# 로컬 제목\n\n로컬 본문.")

    def jina(url):
        raise AssertionError("로컬 성공 시 Jina를 부르면 안 된다")

    ext = extract("https://x/y", local_extractor=local, jina_extractor=jina)
    assert ext.title == "로컬 제목"
    assert ext.url == "https://x/y"
    assert ext.sections[0].paragraphs[0].text == "로컬 본문."


def test_extract_falls_back_to_jina_when_local_returns_none():
    calls = {}

    def local(url):
        return None

    def jina(url):
        calls["jina"] = True
        return ("진 제목", "# 진 제목\n\n폴백 본문.")

    ext = extract("https://x/y", local_extractor=local, jina_extractor=jina)
    assert calls.get("jina") is True
    assert ext.title == "진 제목"
    assert ext.sections[0].paragraphs[0].text == "폴백 본문."


def test_extract_infers_title_from_first_header_when_missing():
    def local(url):
        return (None, "# 헤더가 제목\n\n본문.")

    ext = extract("https://x/y", local_extractor=local, jina_extractor=lambda u: None)
    assert ext.title == "헤더가 제목"
