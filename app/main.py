"""viewserver: FastAPI 엔드포인트 + 원문/분석 2단 웹뷰.

- POST /read            {url} → 세션을 만들고 뷰 URL을 즉시 반환. 작업은 백그라운드.
- GET  /view/{id}       원문 + 분석 2단 HTML. 작업 중이면 있는 것부터 보여준다.
- GET  /view/{id}/status 진행 상태. 뷰 페이지가 폴링한다.
- POST /view/{id}/ask   {question, kind} → 같은 원문 맥락으로 추가 질문

읽을지 말지 먼저 보는 흐름은 따로 있다.

- POST /preview         {url} → 프리뷰를 만들고 프리뷰 URL을 즉시 반환.
- GET  /preview/{id}    성격 카드. 승격된 프리뷰면 뷰로 302.
- POST /preview/{id}/read 프리뷰가 뽑아둔 원문을 물려 세션을 만든다(재추출 없음).

읽기는 수십 초 걸린다. 확장 팝업은 닫히면 문서가 파괴돼 진행 중인 fetch도 죽으므로,
요청을 받는 즉시 id를 돌려주고 실제 작업은 서버가 이어서 한다.
"""

from __future__ import annotations

import asyncio
import datetime
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Config
from app.extractor import ExtractionError, extract
from app.models import Extraction, PreviewSession, Session, Translation
from app.reader import (
    AgentRunner,
    ClaudeAgentRunner,
    ReaderError,
    analyze,
    ask,
    check_locators,
)
from app.preview import PreviewError, estimate_reading, make_preview
from app.store import (
    append_turn,
    fail_stale_previews,
    fail_stale_sessions,
    list_sessions,
    load_preview,
    load_session,
    purge_previews,
    save_preview,
    save_session,
    write_vault_md,
)
from app.translate import needs_translation, translate_extraction
from starlette.concurrency import run_in_threadpool

_APP_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))


class ReadRequest(BaseModel):
    url: str
    # 읽기 전에 정한 질문. 있으면 프리셋 대신 이것만 묻는다.
    questions: list[str] = []


class AskRequest(BaseModel):
    question: str
    kind: str = "ask"


class PreviewRequest(BaseModel):
    """훑어보기는 질문을 받지 않는다. 판단하기 전에 질문을 짜는 건 번거로움이다."""

    url: str


def _new_id() -> str:
    return secrets.token_hex(6)


def _now_iso() -> str:
    # 목록을 이 값으로 정렬한다. 초 단위면 같은 분에 읽은 글끼리 순서가 흔들린다.
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def _today() -> str:
    return datetime.date.today().isoformat()


def create_app(
    *,
    config: Config | None = None,
    extractor: Callable[[str], Extraction] = extract,
    agent: AgentRunner | None = None,
) -> FastAPI:
    config = config or Config.from_env()
    agent = agent or ClaudeAgentRunner(model=config.model)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        fail_stale_sessions(data_dir=config.data_dir)
        fail_stale_previews(data_dir=config.data_dir)
        purge_previews(
            data_dir=config.data_dir, older_than_days=config.preview_ttl_days
        )
        yield

    app = FastAPI(title="readwell", lifespan=lifespan)
    # 확장에 호스트 권한이 없으면(Firefox 임시 로드가 그렇다) 요청이 CORS를 타고,
    # application/json이라 프리플라이트가 붙는다. 오리진은 재로드마다 바뀌는 UUID다.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(moz|chrome)-extension://[a-zA-Z0-9-]+$",
        allow_methods=["POST"],
        allow_headers=["content-type"],
    )
    app.state.config = config
    app.state.extractor = extractor
    app.state.agent = agent
    app.state.tasks = set()
    app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")

    # 번역은 문단마다 따로 저장한다. load → 수정 → save가 겹치면 갱신이 사라진다.
    write_lock = asyncio.Lock()

    async def _update(sid: str, **fields) -> Session:
        async with write_lock:
            session = load_session(sid, data_dir=config.data_dir)
            for key, value in fields.items():
                setattr(session, key, value)
            save_session(session, data_dir=config.data_dir)
            return session

    async def _add_translation(sid: str, locator: str, text: str) -> None:
        async with write_lock:
            session = load_session(sid, data_dir=config.data_dir)
            translation = session.translation or Translation()
            translation.paragraphs[locator] = text
            session.translation = translation
            save_session(session, data_dir=config.data_dir)

    async def _analyze_step(
        sid: str, extraction: Extraction, questions: list[str]
    ) -> None:
        try:
            analysis = await analyze(
                extraction, agent=app.state.agent, questions=questions
            )
        except ReaderError as e:
            # 부분 결과: 추출만으로도 읽을 수 있으니 done으로 둔다.
            await _update(sid, status="done", error=str(e))
            return
        session = await _update(sid, status="done", analysis=analysis)
        if config.write_vault:
            write_vault_md(session, vault_dir=config.vault_dir, date=_today())

    async def _translate_step(sid: str, extraction: Extraction) -> None:
        """한국어가 아닌 글만 문단별로 번역한다. 한국어면 아무것도 안 남긴다."""
        if not needs_translation(extraction):
            return

        count = sum(len(s.paragraphs) for s in extraction.sections)
        limit = config.translate_max_paragraphs
        if count > limit:
            await _update(
                sid,
                translation=Translation(
                    done=True,
                    note=f"문단이 {count}개로 상한 {limit}개를 넘어 번역을 건너뜁니다.",
                ),
            )
            return

        await _update(sid, translation=Translation())
        _, failed = await translate_extraction(
            extraction,
            agent=app.state.agent,
            concurrency=config.translate_concurrency,
            on_paragraph=lambda locator, text: _add_translation(sid, locator, text),
        )
        async with write_lock:
            session = load_session(sid, data_dir=config.data_dir)
            translation = session.translation or Translation()
            translation.done = True
            translation.note = (
                f"문단 {len(failed)}개는 번역하지 못했습니다." if failed else None
            )
            session.translation = translation
            save_session(session, data_dir=config.data_dir)

    async def _run(sid: str, url: str, questions: list[str]) -> None:
        """추출 → 분석. 각 단계가 끝날 때마다 저장해서 뷰가 바로 반영한다."""
        try:
            # extract는 동기 네트워크 호출이라 스레드로 뺀다. 안 그러면 루프가 멈춘다.
            extraction: Extraction = await run_in_threadpool(app.state.extractor, url)
        except ExtractionError as e:
            await _update(sid, status="failed", error=str(e))
            return
        except Exception as e:  # 추출기가 뭘 던지든 뷰에는 사유가 남아야 한다
            await _update(sid, status="failed", error=f"추출 실패: {e}")
            return

        await _update(
            sid,
            status="analyzing",
            url=extraction.url,
            title=extraction.title,
            extraction=extraction,
        )

        await _analyze_and_translate(sid, extraction, questions)

    async def _analyze_and_translate(
        sid: str, extraction: Extraction, questions: list[str]
    ) -> None:
        """분석과 번역은 서로 독립이다. 나란히 돌리고 각자 끝나는 대로 저장한다.

        추출 없이 여기부터 시작하는 경로가 있다. 프리뷰에서 승격할 때다.
        """
        await asyncio.gather(
            _analyze_step(sid, extraction, questions),
            _translate_step(sid, extraction),
        )

    def _spawn(coro) -> None:
        """태스크 참조를 붙들지 않으면 GC가 도중에 걷어갈 수 있다."""
        task = asyncio.create_task(coro)
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)

    async def _update_preview(pid: str, **fields) -> PreviewSession:
        async with write_lock:
            session = load_preview(pid, data_dir=config.data_dir)
            for key, value in fields.items():
                setattr(session, key, value)
            save_preview(session, data_dir=config.data_dir)
            return session

    async def _run_preview(pid: str, url: str) -> None:
        """추출 → 성격 카드. 판단용이라 분석도 번역도 돌리지 않는다."""
        try:
            extraction: Extraction = await run_in_threadpool(app.state.extractor, url)
        except ExtractionError as e:
            await _update_preview(pid, status="failed", error=str(e))
            return
        except Exception as e:
            await _update_preview(pid, status="failed", error=f"추출 실패: {e}")
            return

        chars, minutes = estimate_reading(extraction)
        await _update_preview(
            pid,
            status="previewing",
            url=extraction.url,
            title=extraction.title,
            extraction=extraction,
            char_count=chars,
            read_minutes=minutes,
        )

        try:
            card = await make_preview(
                extraction,
                agent=app.state.agent,
                max_chars=config.preview_max_chars,
            )
        except PreviewError as e:
            # 분량과 제목만으로도 판단이 반쯤은 된다. 실패로 두되 있는 건 보여준다.
            await _update_preview(pid, status="failed", error=str(e))
            return
        await _update_preview(pid, status="done", preview=card)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """읽은 글 목록. 최근 것부터. 번역이 빠진 글은 여기서 바로 걸 수 있다."""
        cfg: Config = request.app.state.config
        items = []
        for session in list_sessions(data_dir=cfg.data_dir):
            extraction = session.extraction
            total = (
                sum(len(s.paragraphs) for s in extraction.sections) if extraction else 0
            )
            translated = len(session.translation.paragraphs) if session.translation else 0
            translatable = extraction is not None and needs_translation(extraction)
            items.append(
                {
                    "session": session,
                    "host": urlparse(session.url).netloc or session.url,
                    "translated": translated,
                    # 번역 대상인데 아직 덜 찬 글. 중단분·실패분 복구도 여기로 한다.
                    "can_translate": translatable and translated < total,
                }
            )
        return _templates.TemplateResponse(request, "index.html", {"items": items})

    @app.post("/read")
    async def read(body: ReadRequest, request: Request):
        cfg: Config = request.app.state.config
        sid = _new_id()
        questions = [q.strip() for q in body.questions if q.strip()]
        save_session(
            Session(
                id=sid,
                url=body.url,
                title=body.url,  # 추출 전이라 제목을 모른다
                created_at=_now_iso(),
                status="extracting",
                questions=questions,
            ),
            data_dir=cfg.data_dir,
        )

        _spawn(_run(sid, body.url, questions))

        return {
            "id": sid,
            "viewUrl": f"{cfg.base_url}/view/{sid}",
            "status": "extracting",
        }

    @app.get("/view/{sid}/status")
    def view_status(sid: str, request: Request):
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        return {"status": session.status, "error": session.error}

    @app.get("/view/{sid}/translations")
    def view_translations(sid: str, request: Request):
        """지금까지 번역된 문단. 뷰가 폴링해 새로 온 것만 DOM에 꽂는다."""
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        translation = session.translation
        if translation is None:  # 한국어 원문이라 번역하지 않는다
            return {"done": True, "paragraphs": {}, "note": None}
        return {
            "done": translation.done,
            "paragraphs": translation.paragraphs,
            "note": translation.note,
        }

    @app.post("/view/{sid}/translate")
    async def translate_route(sid: str, request: Request):
        """이미 읽은 글을 나중에 번역한다.

        번역이 붙기 전에 읽은 세션이나, 중단돼 절반만 찬 세션을 되살리는 용도다.
        분석은 건드리지 않는다.
        """
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        if session.extraction is None:
            raise HTTPException(status_code=409, detail="원문이 없어 번역할 수 없습니다.")
        if not needs_translation(session.extraction):
            return {"status": "skipped", "note": "한국어 원문이라 번역하지 않습니다."}
        if session.translation is not None and not session.translation.done:
            return {"status": "translating"}  # 이미 도는 중

        # 응답하기 전에 열어둬야 뷰 폴링이 "아직 안 끝났다"를 본다.
        await _update(sid, translation=Translation())
        _spawn(_translate_step(sid, session.extraction))
        return {"status": "translating"}

    @app.get("/view/{sid}", response_class=HTMLResponse)
    def view(sid: str, request: Request):
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        invalid = (
            check_locators(session.analysis, session.extraction)
            if session.analysis and session.extraction
            else set()
        )
        return _templates.TemplateResponse(
            request,
            "view.html",
            {
                "session": session,
                "analysis": session.analysis,
                "invalid_locators": invalid,
                "translations": (
                    session.translation.paragraphs if session.translation else {}
                ),
                "translating": bool(
                    session.translation and not session.translation.done
                ),
            },
        )

    # --- 훑어보기 -----------------------------------------------------------

    def _get_preview(pid: str) -> PreviewSession:
        try:
            return load_preview(pid, data_dir=config.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="프리뷰 없음") from e

    @app.post("/preview")
    async def preview_create(body: PreviewRequest):
        pid = _new_id()
        save_preview(
            PreviewSession(
                id=pid,
                url=body.url,
                title=body.url,  # 추출 전이라 제목을 모른다
                created_at=_now_iso(),
                status="extracting",
            ),
            data_dir=config.data_dir,
        )
        _spawn(_run_preview(pid, body.url))
        return {
            "id": pid,
            "previewUrl": f"{config.base_url}/preview/{pid}",
            "status": "extracting",
        }

    @app.get("/preview/{pid}/status")
    def preview_status(pid: str):
        session = _get_preview(pid)
        return {"status": session.status, "error": session.error}

    @app.get("/preview/{pid}", response_class=HTMLResponse)
    def preview_page(pid: str, request: Request):
        session = _get_preview(pid)
        # 이미 읽기로 한 글이다. 판단 화면을 다시 보여줄 이유가 없다.
        if session.promoted_to:
            return RedirectResponse(f"/view/{session.promoted_to}", status_code=302)
        return _templates.TemplateResponse(
            request, "preview.html", {"session": session, "preview": session.preview}
        )

    @app.post("/preview/{pid}/read")
    async def preview_promote(pid: str):
        """프리뷰가 뽑아둔 원문을 물려 세션을 만든다. 추출을 다시 하지 않는다."""
        session = _get_preview(pid)
        if session.promoted_to:
            # 버튼을 두 번 눌러도 세션이 둘 생기면 안 된다.
            return {
                "id": session.promoted_to,
                "viewUrl": f"{config.base_url}/view/{session.promoted_to}",
            }
        if session.status != "done" or session.extraction is None:
            raise HTTPException(
                status_code=409, detail="프리뷰가 아직 끝나지 않았습니다."
            )

        sid = _new_id()
        # 원문을 채워서 먼저 저장한다. 그래야 뷰가 열리자마자 원문을 보여준다.
        save_session(
            Session(
                id=sid,
                url=session.url,
                title=session.title,
                created_at=_now_iso(),
                status="analyzing",
                extraction=session.extraction,
            ),
            data_dir=config.data_dir,
        )
        await _update_preview(pid, promoted_to=sid)
        # 질문은 비운다. 비면 reader가 프리셋 5개로 읽는다.
        _spawn(_analyze_and_translate(sid, session.extraction, []))
        return {"id": sid, "viewUrl": f"{config.base_url}/view/{sid}"}

    @app.post("/view/{sid}/ask")
    async def ask_route(sid: str, body: AskRequest, request: Request):
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        if session.extraction is None:
            raise HTTPException(
                status_code=409, detail="아직 원문을 가져오는 중입니다."
            )
        try:
            turn = await ask(
                session, body.question, agent=request.app.state.agent, kind=body.kind
            )
        except ReaderError as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
        append_turn(sid, turn, data_dir=cfg.data_dir)
        return {"answer": turn.answer, "locators": turn.locators, "kind": turn.kind}

    return app


# uvicorn app.main:app 진입점
app = create_app()
