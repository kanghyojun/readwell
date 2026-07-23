"""URL → 본문 마크다운 추출.

trafilatura(로컬)로 먼저 뽑고, 실패하면 Jina Reader로 폴백한다.
추출한 마크다운은 헤더/빈 줄 기준으로 섹션·문단으로 나눠 각 문단에 locator를 단다.
이 locator가 분석 산출물이 원문으로 되돌아가는 앵커가 된다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
import trafilatura

from app.models import Extraction, Paragraph, Section

JINA_ENDPOINT = "https://r.jina.ai/"
HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# (title | None, markdown) 쌍. 실패 시 None.
ExtractResult = tuple[str | None, str] | None


class ExtractionError(RuntimeError):
    """로컬·폴백 모두 본문을 뽑지 못함."""


# --- 마크다운 분절 -----------------------------------------------------------


def sectionize(markdown: str) -> list[Section]:
    """마크다운을 섹션(헤더 기준)과 문단(빈 줄 기준)으로 나눈다.

    - ATX 헤더(``#``~``######``)가 섹션 경계이자 섹션 제목.
    - 헤더 앞에 본문이 오면 제목 없는 s1에 담긴다.
    - locator는 ``s{섹션}-p{문단}`` 형식.
    """
    sections: list[Section] = []
    cur: Section | None = None
    sec_idx = 0
    para_idx = 0
    buf: list[str] = []

    def start_section(title: str | None) -> None:
        nonlocal cur, sec_idx, para_idx
        sec_idx += 1
        para_idx = 0
        cur = Section(locator=f"s{sec_idx}", title=title, paragraphs=[])
        sections.append(cur)

    def flush() -> None:
        nonlocal buf, para_idx, cur
        if not buf:
            return
        text = " ".join(line.strip() for line in buf).strip()
        buf = []
        if not text:
            return
        if cur is None:
            start_section(None)
        assert cur is not None
        para_idx += 1
        cur.paragraphs.append(
            Paragraph(locator=f"{cur.locator}-p{para_idx}", text=text)
        )

    for line in markdown.splitlines():
        m = HEADER_RE.match(line)
        if m:
            flush()
            start_section(m.group(2).strip())
        elif line.strip() == "":
            flush()
        else:
            buf.append(line)
    flush()
    return sections


def build_extraction(url: str, title: str | None, markdown: str) -> Extraction:
    sections = sectionize(markdown)
    if not title:
        title = next((s.title for s in sections if s.title), None) or url
    return Extraction(title=title, url=url, markdown=markdown, sections=sections)


# --- trafilatura (로컬) ------------------------------------------------------


def markdown_from_html(html: str, url: str | None = None) -> ExtractResult:
    """HTML 문자열에서 본문 마크다운과 제목을 뽑는다. 본문 없으면 None."""
    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        url=url,
    )
    if not md or not md.strip():
        return None
    meta = trafilatura.extract_metadata(html)
    title = getattr(meta, "title", None) if meta else None
    return (title, md)


def _local_extract(url: str) -> ExtractResult:
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return markdown_from_html(downloaded, url=url)


# --- Jina Reader (폴백) ------------------------------------------------------


def _parse_jina(text: str) -> tuple[str | None, str]:
    """Jina Reader 응답의 ``Title:``/``Markdown Content:`` 프리앰블을 벗겨낸다."""
    if not text.startswith("Title:"):
        return (None, text)
    first_nl = text.find("\n")
    if first_nl == -1:
        return (text[len("Title:") :].strip(), "")
    title = text[len("Title:") : first_nl].strip()
    marker = "Markdown Content:"
    if marker in text:
        body = text.split(marker, 1)[1].lstrip("\n")
    else:
        body = text[first_nl + 1 :].lstrip("\n")
    return (title, body)


def jina_markdown(url: str, *, client: httpx.Client | None = None) -> ExtractResult:
    owns = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = client.get(
            JINA_ENDPOINT + url,
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
        )
        resp.raise_for_status()
        text = resp.text
    finally:
        if owns:
            client.close()
    if not text or not text.strip():
        return None
    return _parse_jina(text)


# --- 오케스트레이션 ----------------------------------------------------------


def extract(
    url: str,
    *,
    local_extractor: Callable[[str], ExtractResult] = _local_extract,
    jina_extractor: Callable[[str], ExtractResult] = jina_markdown,
) -> Extraction:
    """로컬 추출 → 실패 시 Jina 폴백 → Extraction."""
    result: ExtractResult
    try:
        result = local_extractor(url)
    except Exception:
        result = None
    if result is None:
        result = jina_extractor(url)
    if result is None:
        raise ExtractionError(f"본문 추출 실패: {url}")
    title, markdown = result
    return build_extraction(url, title, markdown)
