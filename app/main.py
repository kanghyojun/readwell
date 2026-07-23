"""viewserver: FastAPI 엔드포인트 + 원문/분석 2단 웹뷰.

- POST /read           {url} → 추출·분석·저장 후 뷰 URL 반환
- GET  /view/{id}      원문 + 분석 2단 HTML
- POST /view/{id}/ask  {question, kind} → 같은 원문 맥락으로 추가 질문
"""

from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Config
from app.extractor import ExtractionError, extract
from app.models import Extraction, Session
from app.reader import (
    AgentRunner,
    ClaudeAgentRunner,
    ReaderError,
    analyze,
    ask,
    check_locators,
)
from app.store import append_turn, load_session, save_session, write_vault_md

_APP_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))


class ReadRequest(BaseModel):
    url: str


class AskRequest(BaseModel):
    question: str
    kind: str = "ask"


def _new_id() -> str:
    return secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


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

    app = FastAPI(title="readwell")
    app.state.config = config
    app.state.extractor = extractor
    app.state.agent = agent
    app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")

    @app.post("/read")
    async def read(body: ReadRequest, request: Request):
        cfg: Config = request.app.state.config
        try:
            extraction: Extraction = request.app.state.extractor(body.url)
        except ExtractionError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        analysis = None
        invalid: list[str] = []
        error: str | None = None
        try:
            analysis = await analyze(extraction, agent=request.app.state.agent)
            invalid = sorted(check_locators(analysis, extraction))
        except ReaderError as e:
            error = str(e)  # 부분 결과: 추출만 저장

        sid = _new_id()
        session = Session(
            id=sid,
            url=extraction.url,
            title=extraction.title,
            created_at=_now_iso(),
            extraction=extraction,
            analysis=analysis,
        )
        save_session(session, data_dir=cfg.data_dir)
        if cfg.write_vault and analysis is not None:
            write_vault_md(session, vault_dir=cfg.vault_dir, date=_today())

        return {
            "id": sid,
            "viewUrl": f"{cfg.base_url}/view/{sid}",
            "invalidLocators": invalid,
            "error": error,
        }

    @app.get("/view/{sid}", response_class=HTMLResponse)
    def view(sid: str, request: Request):
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
        invalid = (
            check_locators(session.analysis, session.extraction)
            if session.analysis
            else set()
        )
        return _templates.TemplateResponse(
            request,
            "view.html",
            {
                "session": session,
                "analysis": session.analysis,
                "invalid_locators": invalid,
            },
        )

    @app.post("/view/{sid}/ask")
    async def ask_route(sid: str, body: AskRequest, request: Request):
        cfg: Config = request.app.state.config
        try:
            session = load_session(sid, data_dir=cfg.data_dir)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="세션 없음") from e
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
