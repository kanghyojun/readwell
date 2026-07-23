# readwell 설계

작성일: 2026-07-11
상태: 기획 확정, 구현 계획(plan) 착수 전

## 무엇을 만드나

블로그 등 긴 글을 요약으로 뭉개지 않고, 원문 회귀용 색인과 검증으로 빠르게 읽는 개인 도구다.
AI는 Claude Agent SDK(구독 인증)로 돌린다.

핵심 원칙 하나: **요약이 원문을 대체하지 않는다.**
모든 AI 산출물에는 원문 문장 인용과 위치를 붙여, 요약이 아니라 원문으로 가는 앵커로 기능하게 한다.
색인이든 비판이든 클릭하면 원문 그 자리로 점프한다.

방법론 전문은 [methodology.md](./methodology.md) 참고.

## 사용자와 환경

- 개인용 단독 도구. 사용자 1인(강효준).
- 읽기는 Mac 브라우저/Obsidian, 처리는 Linux headless(`hops-dev-2`), 저장은 mac-handoff vault(Syncthing).
- Agent SDK 구독 인증은 Linux의 `claude` OAuth를 그대로 물려받는다. 개인용으로만 쓴다(대량 호출 금지).
- Mac ↔ Linux 연결은 Tailscale. Mac(`macbookpro`)에서 `hops-dev-2`(`100.99.117.44`)로 닿는다.

## 아키텍처

```
[확장(Mac)] ──Tailscale HTTP──▶ [웹서비스(Linux, hops-dev-2)]
   트리거                          본문 추출 → Agent SDK 방법론 → 웹뷰 + vault md
                                            │
                                   [웹뷰(브라우저)] 원문 + 분석 2단, 대화형
```

| 컴포넌트 | 역할 | 어디서 도나 |
|---|---|---|
| 브라우저 확장 | 현재 탭 URL을 웹서비스로 POST, 결과 뷰를 새 탭으로 엶 | Mac 브라우저 |
| 웹서비스 | 본문 추출 + 방법론 실행 + 저장 + 뷰 서빙 | Linux(hops-dev-2), 포트 2000번대 |
| 웹뷰 | 원문 + 분석을 나란히 보고 추가 질문 | 브라우저(Mac) |

## 컴포넌트 상세

### 1. 브라우저 확장 (Mac)

- Manifest V3.
- 역할: 현재 탭 URL을 웹서비스 `POST /read`로 보내고, 응답의 뷰 URL을 새 탭으로 연다.
- 권한 최소화: `activeTab`, 웹서비스 호스트(tailnet) 접근.
- v1은 URL만 보낸다. 선택 텍스트/DOM 본문 전송은 v2.
- 개발/로드는 Mac에서 한다(headless Linux에선 확장을 못 띄운다). 코드는 이 repo에서 짜고 Mac으로 동기화.

### 2. 웹서비스 (Linux)

FastAPI. 하위 모듈은 역할별로 나눈다.

- **extractor**: URL을 본문 md로. trafilatura(로컬)를 먼저 쓰고 실패하면 Jina(`r.jina.ai`) 폴백. 반환은 `{title, markdown, sections[]}`. sections는 앵커(위치 식별자)의 근거가 된다.
- **reader**: claude-agent-sdk로 방법론을 실행한다. 입력은 본문 md, 출력은 분석 JSON(아래 스키마). 모든 항목에 원문 인용과 locator를 강제한다.
- **store**: 결과를 vault에 사람이 읽는 md로, 로컬에 재방문용 json으로 저장한다.
- **viewserver**: 원문 + 분석 2단 HTML을 렌더하고 추가 질문 API를 연다.

HTTP 엔드포인트(초안):
- `POST /read {url}` → 추출·분석·저장 후 뷰 URL 반환
- `GET /view/{id}` → 원문 + 분석 2단 HTML
- `POST /view/{id}/ask {question}` → 추가 질문. 같은 원문 맥락으로 Agent 재호출, 원문 인용으로 답

### 3. 웹뷰

- 서버 렌더 HTML + 가벼운 JS(앵커 점프, 추가 질문). SPA는 과하다.
- 좌: 원문 md 렌더. 우: 분석 탭(색인 / 질문 / 비판 / 매핑).
- 앵커 클릭하면 좌측 원문의 해당 위치로 스크롤.
- 하단에 추가 질문 입력(Feynman 검증, 개념 글로싱 포함).

## 방법론 → 기능 매핑

| 방법론(methodology.md) | 어디서 구현 | 첫 화면 여부 |
|---|---|---|
| 요지 색인(gist) | reader 색인 산출 | 첫 화면 |
| 3-Pass 1단계 스캔 | reader 스캔 한 문단(색인 상단) | 첫 화면 |
| 질문 기반 추출 | 프리셋 질문 자동 + 추가 질문 API | 프리셋은 첫 화면 |
| 비판적 읽기 | reader 비판 산출 | 첫 화면 |
| 주장-근거 매핑 | reader 매핑 산출 | 첫 화면 |
| Feynman 검증 | 추가 질문 모드(내 이해 붙여넣기) | 버튼/입력 |
| 개념 글로싱 | 추가 질문(선택 용어 풀이) | 버튼/입력 |

첫 화면은 "풀 분석"이다(색인 + 프리셋 질문 + 비판 + 매핑). 대신 전부 앵커라 요약 소비로 흐르지 않는다.

## 분석 출력 스키마 (reader → 웹뷰/저장)

```jsonc
{
  "scan": "이 글이 전체적으로 무엇에 관한지 한 문단",
  "gist": [
    { "section": "섹션 제목/번호", "oneLine": "한 줄 요지", "locator": "s2-p3" }
  ],
  "claims": [
    { "claim": "핵심 주장", "evidence": "근거", "locator": "s3-p1", "weakness": "약한 지점(없으면 생략)" }
  ],
  "questions": [   // 프리셋 질문의 답. AI 요약이 아니라 원문 인용
    { "q": "이 글의 핵심 주장은?", "answerQuote": "원문 문장 그대로", "locator": "s1-p2" }
  ],
  "critique": [
    { "hiddenPremise": "숨은 전제", "weakEvidence": "약한 근거", "missingCounterexample": "빠진 반례", "locator": "s4-p2" }
  ]
}
```

- `locator`는 섹션/문단 식별자다. extractor가 원문 md를 나눌 때 부여하고, 웹뷰가 앵커로 매핑한다.
- reader는 스키마 검증을 통과해야 한다. 특히 각 항목의 locator가 실재하는 위치를 가리키는지 확인한다.

## 저장 레이아웃

- vault(사람이 읽는 결과): `mac-handoff/readwell/YYYY-MM-DD-<slug>.md`
- 로컬 세션(재방문용): `~/src/readwell/data/<id>.json` (원문 + 분석 + 대화 누적)

## 에러 처리

| 상황 | 처리 |
|---|---|
| 추출 실패(페이월/JS 렌더링) | Jina 폴백, 그래도 안 되면 웹뷰에 안내(확장 DOM 긁기는 v2) |
| Agent 실패/한도 초과 | 에러 표시하되 부분 결과라도 저장 |
| 구독 사용량 한도 | 개인용 전제. 배치 남용 안 함 |

## 테스트 전략

- extractor: 샘플 블로그 HTML 픽스처로 본문 추출 단위 테스트
- reader: Agent 출력 스키마 검증 + locator 유효성(실재하는 위치인지)
- 통합: URL 하나 넣어 md 생성까지 스모크

## 기술 스택

- 웹서비스: Python 3.11, FastAPI, uvicorn, claude-agent-sdk, trafilatura, httpx(Jina 폴백)
- 확장: vanilla JS, Manifest V3
- 포트: 2000번대
- 저장: mac-handoff vault + 로컬 json

## 범위 밖 (YAGNI)

Tauri 앱화, 다중 사용자, 로그인/페이월 콘텐츠(v2), 대량 배치 처리.

## 결정 필요 (미결) → 전부 해소

구현 착수 시 아래로 결정했다. 상세는 [구현 계획·결정 로그](./2026-07-11-readwell-plan.md) 참고.

1. **본문 추출 위치**: 서버 fetch로 시작(추천안). 확장 DOM 긁기는 v2.
2. **프리셋 질문 세트**: 방법론 "질문 기반 추출" 기반 5개로 고정(`app/reader.py`의 `PRESET_QUESTIONS`).
3. **웹뷰 대화 상태**: `data/<id>.json`에 무제한 누적(개인용).
4. **claude-agent-sdk 최신 API**: `ClaudeAgentOptions(output_format={"type":"json_schema","schema":...})`로
   구조화 출력을 요청하고 `ResultMessage.structured_output`으로 회수. 구독 OAuth는 SDK가
   `~/.claude/.credentials.json`을 자동으로 사용. 실호출로 검증 완료.
