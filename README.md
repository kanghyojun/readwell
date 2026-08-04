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
- **목록(`/`)**: 지금까지 읽은 글이 최근 순으로. 제목을 누르면 뷰로 가고,
  번역이 빠진 외국어 글은 목록에서 바로 번역을 걸 수 있다.

읽기 요청은 오래 걸린다(수십 초). `POST /read`는 세션만 만들고 뷰 URL을 즉시 돌려주며,
추출·분석·번역은 서버 백그라운드에서 이어진다. 뷰 페이지가 상태를 폴링해 끝난 것부터
채운다. 확장 팝업은 닫히면 진행 중인 `fetch`가 죽기 때문에 이 구조가 필요하다.

### 읽기 전 질문

방법론에서 가장 효과가 큰 건 "질문 기반 추출"이다. 읽기 전에 알고 싶은 걸 정해두면
그 질문의 답을 원문 인용으로 받는다. 확장 팝업의 입력칸에 한 줄에 하나씩 적는다.

```
POST /read  {"url": "...", "questions": ["SIMD를 언제 써야 하나?", "성능 이득은?"]}
```

질문을 주면 프리셋 5개 대신 그 질문만 묻는다. 비우면 지금까지처럼 프리셋으로 읽는다.
읽기 전에 질문이 안 떠오르면 그냥 비우고, 뷰의 추가질문 탭에서 물어도 된다.

### 번역

한국어가 아닌 글은 문단마다 번역해 원문 바로 아래에 붙인다(immersive translate 방식).
상단 토글로 원문+번역 / 원문만 / 번역만을 고른다.

- 판정은 LLM 없이 글자 비율로 한다. 글 전체에서 한글이 10% 미만이면 번역 대상이다.
  문단 하나가 아니라 글 전체를 보므로, 영어 용어나 인용이 섞인 한국어 글은 번역하지 않는다.
- 문단마다 따로 호출한다. 하나가 실패해도 나머지는 살고, 끝나는 대로 화면에 붙는다.
  대신 문단 사이 문맥이 공유되지 않아 용어가 조금씩 달라질 수 있다.
- **문단 수가 그대로 호출 수다.** 문단 80개짜리 글이면 번역만 80번 호출한다.
  `READWELL_TRANSLATE_MAX_PARAGRAPHS`(기본 150) 상한을 넘으면 번역을 건너뛰고 사유를 표시한다.

이미 읽어둔 글은 나중에 번역할 수 있다. 목록(`/`)의 **번역** 버튼이나 `POST /view/{id}/translate`다.
번역이 붙기 전에 읽은 글, 중단된 번역, 일부만 실패한 번역을 이걸로 되살린다. 분석은 건드리지 않는다.
한국어 글에 걸면 `{"status": "skipped"}`로 돌아온다.

### 프롬프트 구분자

**LLM에 넘기는 입력은 `=== 본문 시작 ===` 같은 구분선 대신 XML 태그로 감싼다.**
구분선은 입력에도 출력에도 어울리는 모양이라 모델이 대칭으로 흉내낸다. 실제로 번역에서
프롬프트에 없던 `=== 번역 시작 === … === 번역 끝 ===`을 지어내 번역문을 감쌌고, 그게
`text` 필드에 담겨 화면에 그대로 나왔다. XML 태그는 입력 구조를 나타내는 관례라 답변에
따라붙지 않는다. 태그마다 무슨 역할인지도 프롬프트에 적는다. 그래야 제목이나 URL을
본문으로 착각하지 않는다.

프롬프트로 막아도 확률이 줄 뿐이라 `strip_prompt_echo()`가 저장 전에 한 번 더 걷어낸다.
번역문, 분석의 `scan`, 추가질문의 `answer`처럼 자유 서술 필드에만 건다. 원문을 그대로
옮기는 인용 필드(`answerQuote`)에는 걸지 않는다. 원문에 그런 줄이 있었다면 지우는 쪽이 손해다.

이 규칙은 `tests/test_reader.py`와 `tests/test_translate.py`가 지킨다.

## 문서

- [docs/methodology.md](./docs/methodology.md) — AI로 긴 글 잘 읽기: 방법론
- [docs/2026-07-11-readwell-design.md](./docs/2026-07-11-readwell-design.md) — 설계 스펙
- [docs/2026-07-11-readwell-plan.md](./docs/2026-07-11-readwell-plan.md) — 구현 계획·결정·진행 로그

## 실행

```bash
uv sync                 # 의존성 설치
uv run pytest -q        # 테스트 (102개)

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
| `READWELL_TRANSLATE_MAX_PARAGRAPHS` | 150 | 이 수를 넘으면 번역을 건너뛴다 |
| `READWELL_TRANSLATE_CONCURRENCY` | 6 | 번역 동시 호출 수 |

md를 다른 곳에 쌓으려면 `READWELL_VAULT_DIR`을 바꿔 기동한다. 경로의 `~`는 펴진다.

```bash
READWELL_VAULT_DIR="~/Documents/obsidian/읽은글" \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 2100
```

- Agent는 `~/.claude/.credentials.json`의 구독 OAuth를 그대로 쓴다. 개인용 전제, 대량 호출 금지.
- 브라우저 확장은 `extension/`을 Mac Chrome 개발자 모드로 로드한다. [extension/README.md](./extension/README.md) 참고.

## 구현 상태

v1 구현 완료. 테스트 42개 통과. 실제 URL(paulgraham.com/todo.html)로 추출→분석→저장→뷰까지
end-to-end 검증. agent 구조화 출력(json_schema)과 locator 실재성 검증 동작 확인.

- 웹서비스(Linux): extractor / reader / store / viewserver + 웹뷰 — 동작.
- 브라우저 확장(MV3): 코드 완성. 로드·구동은 Mac에서(headless라 여기선 미검증).
- 범위 밖(v2): 확장 DOM 긁기(페이월), Tauri, 다중 사용자.
