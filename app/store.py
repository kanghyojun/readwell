"""저장: 재방문용 json(data/) + 사람이 읽는 vault md.

- ``data/<id>.json``: 원문 + 분석 + 대화 누적. 웹뷰 재방문·추가질문의 근거.
- ``data/previews/<id>.json``: 훑어보기 한 건. TTL이 지나면 지운다.
- vault md: mac-handoff/readwell/YYYY-MM-DD-<slug>.md. 사람이 읽는 결과.

프리뷰를 하위 디렉터리에 두는 이유는 목록이 세션만 보게 하기 위해서다. 같은 곳에
두면 목록에서 걸러내는 코드가 필요하고, 거르는 걸 한 군데서 빠뜨리면 판단하고
버린 글이 읽은 글 목록에 섞인다.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from app.models import ChatTurn, PreviewSession, Session

_PREVIEW_SUBDIR = "previews"

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


def _atomic_write_json(path: Path, payload: str) -> Path:
    """원자적으로 쓴다.

    작업이 백그라운드에서 도는 동안 페이지는 같은 파일을 폴링한다. 그냥 덮어쓰면
    쓰는 도중에 읽는 쪽이 잘린 JSON을 보게 된다. 같은 디렉터리에 임시 파일로 쓰고
    os.replace로 바꿔치기하면 읽는 쪽은 항상 온전한 파일을 본다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def save_session(session: Session, *, data_dir: Path | str) -> Path:
    return _atomic_write_json(
        _session_path(data_dir, session.id),
        session.model_dump_json(by_alias=True, indent=2),
    )


def load_session(sid: str, *, data_dir: Path | str) -> Session:
    path = _session_path(data_dir, sid)
    if not path.exists():
        raise FileNotFoundError(f"세션 없음: {sid}")
    return Session.model_validate_json(path.read_text(encoding="utf-8"))


def list_sessions(*, data_dir: Path | str) -> list[Session]:
    """읽은 글 목록. 최근에 읽은 것부터. 손상된 파일은 건너뛴다."""
    directory = Path(data_dir)
    if not directory.exists():
        return []
    sessions: list[Session] = []
    for path in directory.glob("*.json"):
        try:
            sessions.append(Session.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions


def fail_stale_sessions(*, data_dir: Path | str) -> int:
    """중단된 작업을 실패로 표시하고 개수를 돌려준다.

    작업은 서버 프로세스 안에서만 돈다. 재시작하면 진행 중이던 세션은 영영
    끝나지 않는데, 상태가 그대로면 뷰 페이지가 끝없이 폴링한다.
    """
    directory = Path(data_dir)
    if not directory.exists():
        return 0
    cleaned = 0
    for path in sorted(directory.glob("*.json")):
        try:
            session = Session.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue  # 손상된 파일은 건드리지 않는다
        changed = False
        if session.pending:
            session.status = "failed"
            session.error = "서버가 재시작되어 중단됐습니다. 다시 시도하세요."
            changed = True
        # 번역은 status와 따로 논다. 열어두면 뷰가 끝없이 폴링한다.
        if session.translation is not None and not session.translation.done:
            session.translation.done = True
            session.translation.note = "서버가 재시작되어 번역이 중단됐습니다."
            changed = True
        if changed:
            save_session(session, data_dir=directory)
            cleaned += 1
    return cleaned


def append_turn(sid: str, turn: ChatTurn, *, data_dir: Path | str) -> Session:
    session = load_session(sid, data_dir=data_dir)
    session.conversation.append(turn)
    save_session(session, data_dir=data_dir)
    return session


# --- 프리뷰 -----------------------------------------------------------------


def _preview_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / _PREVIEW_SUBDIR


def _preview_path(data_dir: Path | str, pid: str) -> Path:
    return _preview_dir(data_dir) / f"{pid}.json"


def save_preview(preview: PreviewSession, *, data_dir: Path | str) -> Path:
    return _atomic_write_json(
        _preview_path(data_dir, preview.id),
        preview.model_dump_json(by_alias=True, indent=2),
    )


def load_preview(pid: str, *, data_dir: Path | str) -> PreviewSession:
    path = _preview_path(data_dir, pid)
    if not path.exists():
        raise FileNotFoundError(f"프리뷰 없음: {pid}")
    return PreviewSession.model_validate_json(path.read_text(encoding="utf-8"))


def purge_previews(*, data_dir: Path | str, older_than_days: int = 7) -> int:
    """TTL이 지난 프리뷰를 지우고 개수를 돌려준다.

    승격 여부는 보지 않는다. 승격된 프리뷰의 원문은 세션이 이미 갖고 있어서,
    프리뷰 파일이 하는 일은 옛 URL을 뷰로 넘겨주는 것뿐이다. 그 편의는 유효기간이 있다.
    """
    directory = _preview_dir(data_dir)
    if not directory.exists():
        return 0
    cutoff = datetime.datetime.now() - datetime.timedelta(days=older_than_days)
    removed = 0
    for path in sorted(directory.glob("*.json")):
        try:
            session = PreviewSession.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            created = datetime.datetime.fromisoformat(session.created_at)
        except ValueError:
            continue  # 손상된 파일은 건드리지 않는다
        if created < cutoff:
            path.unlink()
            removed += 1
    return removed


def fail_stale_previews(*, data_dir: Path | str) -> int:
    """중단된 프리뷰를 실패로 표시하고 개수를 돌려준다.

    세션과 같은 이유다. 재시작하면 진행 중이던 작업은 영영 끝나지 않는데,
    상태가 그대로면 페이지가 끝없이 폴링한다.
    """
    directory = _preview_dir(data_dir)
    if not directory.exists():
        return 0
    cleaned = 0
    for path in sorted(directory.glob("*.json")):
        try:
            session = PreviewSession.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValueError:
            continue
        if not session.pending:
            continue
        session.status = "failed"
        session.error = "서버가 재시작되어 중단됐습니다. 다시 시도하세요."
        save_preview(session, data_dir=data_dir)
        cleaned += 1
    return cleaned


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
