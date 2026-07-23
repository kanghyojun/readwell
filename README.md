# readwell

블로그 등 긴 글을 요약으로 뭉개지 않고, **원문 회귀용 색인과 검증**으로 빠르게 읽는 개인 도구.
AI는 Claude Agent SDK(구독 인증)로 돌린다.

핵심 원칙: 요약이 원문을 대체하지 않는다. 모든 AI 산출물에 원문 인용과 위치를 붙여, 원문으로 가는 앵커로 쓴다.

## 구조

```
[확장(Mac)] ──Tailscale HTTP──▶ [웹서비스(Linux, hops-dev-2)]
   트리거                          본문 추출 → Agent SDK 방법론 → 웹뷰 + vault md
                                            │
                                   [웹뷰(브라우저)] 원문 + 분석 2단, 대화형
```

- **확장(Mac)**: 현재 탭 URL을 웹서비스로 POST, 결과 뷰를 새 탭으로 연다.
- **웹서비스(Linux)**: 본문 추출(trafilatura/Jina) → Agent SDK 방법론 → vault에 md 저장 + 웹뷰 서빙.
- **웹뷰**: 왼쪽 원문, 오른쪽 분석(색인·질문·비판·매핑). 앵커 클릭하면 원문으로 점프. 추가 질문 가능.

## 문서

- [docs/methodology.md](./docs/methodology.md) — AI로 긴 글 잘 읽기: 방법론
- [docs/2026-07-11-readwell-design.md](./docs/2026-07-11-readwell-design.md) — 설계 스펙
- [docs/2026-07-11-readwell-plan.md](./docs/2026-07-11-readwell-plan.md) — 구현 계획·결정·진행 로그

## 실행

```bash
uv sync                 # 의존성 설치
uv run pytest -q        # 테스트 (42개)

# 웹서비스 기동 (포트 2100). base_url은 확장이 뷰를 새 탭으로 열 때 쓴다.
READWELL_BASE_URL="http://100.99.117.44:2100" \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 2100
```

환경변수:

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `READWELL_PORT` | 2100 | 포트 |
| `READWELL_BASE_URL` | (빈값) | 뷰 URL 접두. 확장이 새 탭 열 때 필요 |
| `READWELL_DATA_DIR` | `./data` | 세션 json 저장 위치 |
| `READWELL_VAULT_DIR` | `~/src/mac-handoff/readwell` | vault md 저장 위치 |
| `READWELL_WRITE_VAULT` | 1 | `0`이면 vault md 미저장 |
| `READWELL_MODEL` | (CLI 기본) | agent 모델 오버라이드 |

- Agent는 `~/.claude/.credentials.json`의 구독 OAuth를 그대로 쓴다. 개인용 전제, 대량 호출 금지.
- 브라우저 확장은 `extension/`을 Mac Chrome 개발자 모드로 로드한다. [extension/README.md](./extension/README.md) 참고.

## 구현 상태

v1 구현 완료. 테스트 42개 통과. 실제 URL(paulgraham.com/todo.html)로 추출→분석→저장→뷰까지
end-to-end 검증. agent 구조화 출력(json_schema)과 locator 실재성 검증 동작 확인.

- 웹서비스(Linux): extractor / reader / store / viewserver + 웹뷰 — 동작.
- 브라우저 확장(MV3): 코드 완성. 로드·구동은 Mac에서(headless라 여기선 미검증).
- 범위 밖(v2): 확장 DOM 긁기(페이월), Tauri, 다중 사용자.
