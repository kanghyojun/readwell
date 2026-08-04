"""viewserver: FastAPI 엔드포인트 + 원문/분석 2단 웹뷰.

- POST /read            {url} → 세션을 만들고 뷰 URL을 즉시 반환. 작업은 백그라운드.
- GET  /view/{id}       원문 + 분석 2단 HTML. 작업 중이면 있는 것부터 보여준다.
- GET  /view/{id}/status 진행 상태. 뷰 페이지가 폴링한다.
- POST /view/{id}/ask   {question, kind} → 같은 원문 맥락으로 추가 질문

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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Config
from app.extractor import ExtractionError, extract
from app.models import Extraction, Session, Translation
from app.reader import (
    AgentRunner,
    ClaudeAgentRunner,
    ReaderError,
    analyze,
    ask,
    check_locators,
)
from app.store import (
    append_turn,
    fail_stale_sessions,
    list_sessions,
    load_session,
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

        # 분석과 번역은 서로 독립이다. 나란히 돌리고 각자 끝나는 대로 저장한다.
        await asyncio.gather(
            _analyze_step(sid, extraction, questions),
            _translate_step(sid, extraction),
        )

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

        # 태스크 참조를 붙들지 않으면 GC가 도중에 걷어갈 수 있다.
        task = asyncio.create_task(_run(sid, body.url, questions))
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)

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
        task = asyncio.create_task(_translate_step(sid, session.extraction))
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
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
