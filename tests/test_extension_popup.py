"""팝업이 '읽기 전 질문'을 서버로 실어 보내는지 정적으로 검사한다.

확장은 브라우저에서만 돌아 여기서 실행할 수 없다. 배선이 통째로 빠지는 것만이라도
잡는 게 목적이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_EXT = Path(__file__).resolve().parent.parent / "extension"


@pytest.fixture(scope="module")
def popup_html() -> str:
    return (_EXT / "popup.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def popup_js() -> str:
    return (_EXT / "popup.js").read_text(encoding="utf-8")


def test_popup_has_question_input(popup_html: str) -> None:
    assert 'id="questions"' in popup_html


def test_popup_reads_the_question_input(popup_js: str) -> None:
    assert 'getElementById("questions")' in popup_js


def test_popup_splits_questions_by_line(popup_js: str) -> None:
    """한 줄에 질문 하나. 줄 단위로 쪼개 보내야 서버가 목록으로 받는다."""
    assert "split(" in popup_js


def test_read_sends_the_questions_field(popup_js: str) -> None:
    read_handler = popup_js.split("readBtn.addEventListener")[1].split(");")[0]
    assert "questions" in read_handler


def test_popup_links_to_the_reading_list(popup_html: str) -> None:
    assert 'id="list"' in popup_html


def test_popup_opens_the_reading_list_on_the_server(popup_js: str) -> None:
    assert 'getElementById("list")' in popup_js


# --- 훑어보기 버튼 -----------------------------------------------------------


def test_popup_has_a_skim_button(popup_html: str) -> None:
    assert 'id="skim"' in popup_html


def test_skim_posts_to_the_preview_endpoint(popup_js: str) -> None:
    assert '"/preview"' in popup_js


def test_skim_does_not_send_questions(popup_js: str) -> None:
    """훑어보기는 질문을 받지 않는다. 판단하기 전에 질문을 짜는 건 번거로움이다."""
    skim_handler = popup_js.split("skimBtn.addEventListener")[1].split(");")[0]
    assert '"/preview"' in skim_handler
    assert "questions" not in skim_handler


def test_skim_opens_the_preview_url_in_a_tab(popup_js: str) -> None:
    assert "previewUrl" in popup_js
