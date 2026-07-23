"""저장: 재방문용 json(data/) + 사람이 읽는 vault md.

- ``data/<id>.json``: 원문 + 분석 + 대화 누적. 웹뷰 재방문·추가질문의 근거.
- vault md: mac-handoff/readwell/YYYY-MM-DD-<slug>.md. 사람이 읽는 결과.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import ChatTurn, Session

_FORBIDDEN = re.compile(r'[\\/:*?"<>|]+')
_SPACES = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")


def slugify_title(title: str, max_len: int = 50) -> str:
    """파일명용 슬러그. 한글은 유지하고 공백은 하이픈으로."""
    s = title.strip().lower()
    s = _FORBIDDEN.sub("", s)
    s = _SPACES.sub("-", s)
    s = _DASHES.sub("-", s).strip("-")
    return s[:max_len] or "untitled"


# --- json 세션 --------------------------------------------------------------


def _session_path(data_dir: Path | str, sid: str) -> Path:
    return Path(data_dir) / f"{sid}.json"


def save_session(session: Session, *, data_dir: Path | str) -> Path:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(data_dir, session.id)
    path.write_text(
        session.model_dump_json(by_alias=True, indent=2), encoding="utf-8"
    )
    return path


def load_session(sid: str, *, data_dir: Path | str) -> Session:
    path = _session_path(data_dir, sid)
    if not path.exists():
        raise FileNotFoundError(f"세션 없음: {sid}")
    return Session.model_validate_json(path.read_text(encoding="utf-8"))


def append_turn(sid: str, turn: ChatTurn, *, data_dir: Path | str) -> Session:
    session = load_session(sid, data_dir=data_dir)
    session.conversation.append(turn)
    save_session(session, data_dir=data_dir)
    return session


# --- vault 마크다운 (사람용) -------------------------------------------------


def render_markdown(session: Session) -> str:
    a = session.analysis
    lines: list[str] = [
        f"# {session.title}",
        "",
        f"- 원문: {session.url}",
        f"- 읽은 날: {session.created_at}",
        "",
        "> 요약이 원문을 대체하지 않는다. 각 항목의 `locator`는 원문으로 돌아가는 앵커다.",
        "",
    ]
    if a is None:
        lines += ["_분석 없음(추출만 저장됨)._", ""]
    else:
        lines += ["## 스캔", "", a.scan, ""]

        if a.gist:
            lines += ["## 요지 색인", "", "| 섹션 | 한 줄 요지 | 위치 |", "|---|---|---|"]
            for g in a.gist:
                lines.append(f"| {g.section} | {g.one_line} | `{g.locator}` |")
            lines.append("")

        if a.claims:
            lines += ["## 주장 – 근거", ""]
            for c in a.claims:
                lines.append(f"- **{c.claim}** — {c.evidence} (`{c.locator}`)")
                if c.weakness:
                    lines.append(f"  - 약한 지점: {c.weakness}")
            lines.append("")

        if a.questions:
            lines += ["## 프리셋 질문", ""]
            for q in a.questions:
                lines.append(f"- **{q.q}**")
                lines.append(f"  - > {q.answer_quote} (`{q.locator}`)")
            lines.append("")

        if a.critique:
            lines += ["## 비판적 읽기", ""]
            for cr in a.critique:
                lines.append(f"- 숨은 전제: {cr.hidden_premise} (`{cr.locator}`)")
                lines.append(f"  - 약한 근거: {cr.weak_evidence}")
                lines.append(f"  - 빠진 반례: {cr.missing_counterexample}")
            lines.append("")

    if session.conversation:
        lines += ["## 대화", ""]
        for turn in session.conversation:
            lines.append(f"**Q ({turn.kind}). {turn.question}**")
            locs = " ".join(f"`{loc}`" for loc in turn.locators)
            suffix = f" {locs}" if locs else ""
            lines.append(f"A. {turn.answer}{suffix}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_vault_md(session: Session, *, vault_dir: Path | str, date: str) -> Path:
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    path = vault_dir / f"{date}-{slugify_title(session.title)}.md"
    path.write_text(render_markdown(session), encoding="utf-8")
    return path
