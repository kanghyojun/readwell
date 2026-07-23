# readwell 구현 계획 · 진행 로그

작성: 2026-07-11 (자율 구현 세션)
기준 문서: [설계 스펙](./2026-07-11-readwell-design.md), [방법론](./methodology.md)

이 문서는 8시간 자율 구현 세션의 계획과 결정, 진행 상황을 남긴다.
자리 비운 사이 무엇을/왜 그렇게 정했는지 여기서 확인하면 된다.

## 미결 4개 → 결정

1. **본문 추출 위치** → 서버 fetch로 시작(스펙 추천안). 확장 DOM 긁기는 v2.
2. **프리셋 질문 세트** → 방법론 "질문 기반 추출" 기반 5개로 고정:
   1. 이 글의 핵심 주장은 무엇인가?
   2. 그 주장을 뒷받침하는 가장 강한 근거는?
   3. 기존 통념이나 대안과 다른 지점은?
   4. 독자가 실제로 취할 수 있는 행동/결론은?
   5. 저자가 스스로 인정하는 한계나 조건은?
   - 답은 AI 요약이 아니라 원문 인용 + locator.
3. **웹뷰 대화 상태** → `data/<id>.json`에 대화 이력 무제한 누적. 개인용이라 용량 걱정 없음.
4. **claude-agent-sdk 최신 API** → 확인 완료:
   - `ClaudeAgentOptions(output_format={"type":"json_schema","schema":{...}})`로 구조화 출력 요청.
   - 결과는 `ResultMessage.structured_output`(dict)로 회수.
   - 구독 OAuth는 SDK가 `~/.claude/.credentials.json`을 자동 사용. 별도 API 키 불필요.
   - 개인용 전제, 대량 호출 금지.

## 아키텍처(구현 형태)

```
readwell/
├── pyproject.toml            # uv 프로젝트
├── app/
│   ├── config.py             # 포트·경로·Jina 설정 (env override)
│   ├── models.py             # pydantic: Analysis, Section, Extraction …
│   ├── extractor.py          # URL → {title, markdown, sections[locator]}
│   ├── reader.py             # agent-sdk 방법론 → Analysis (+ ask 추가질문)
│   ├── store.py              # vault md + data/<id>.json
│   ├── main.py               # FastAPI: /read /view/{id} /view/{id}/ask
│   ├── templates/view.html
│   └── static/{view.js,view.css}
├── tests/                    # pytest, HTML 픽스처
├── extension/                # MV3 (Mac에서 로드)
└── data/                     # 로컬 세션 json
```

## 방침

- TDD(Red-Green-Refactor). agent 실호출은 인터페이스로 추상화해 모킹, 실호출 스모크는 마지막 1회.
- 포트 2000번대(기본 2100).
- Linux headless에서 검증 가능한 웹서비스부터. 확장은 코드만.

## 진행 상황 — 전부 완료

- [x] 환경·agent-sdk API 확인
- [x] 프로젝트 셋업 (uv, FastAPI, 의존성)
- [x] models (pydantic 스키마, camelCase alias, locator 검증)
- [x] extractor (trafilatura + Jina 폴백, 섹션/문단 locator)
- [x] store (세션 json 왕복, 대화 누적, vault md 렌더)
- [x] reader (agent-sdk 구조화 출력, 스키마·locator 검증, 프리셋/추가질문)
- [x] viewserver + 웹뷰 (2단 HTML, 앵커 점프, 추가질문)
- [x] 브라우저 확장 (MV3 팝업/옵션)
- [x] 통합 스모크 + 문서

## 검증 결과

- 단위·통합 테스트 **42개 통과** (models 7 / extractor 11 / store 8 / reader 10 / api 6).
- 실제 uvicorn 기동 후 `/view` 렌더·static·404 확인.
- **실제 end-to-end 1회**: `POST /read https://paulgraham.com/todo.html`
  → trafilatura 실추출 → 실제 agent(구독 OAuth) 구조화 출력 → 저장 → 뷰.
  `invalidLocators` 빈 배열(agent가 실재 위치만 사용), scan은 한국어 안내,
  프리셋 질문 답은 영어 원문 그대로 인용 + locator. 방법론 원칙대로 동작.

## 남은 것 / 다음

- 브라우저 확장은 Mac Chrome에서 로드·구동 확인 필요(headless라 여기선 미검증).
- 서버 상시 기동은 systemd/tmux 등으로 별도 구성(README의 uvicorn 명령).
- v2: 확장 DOM 긁기(페이월), 원문 인라인 마크다운 서식 렌더, 대화 이력 정리 정책.
- 커밋은 강효준 확인 후. 현재 워킹트리에만 있음.
