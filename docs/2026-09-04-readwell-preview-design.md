# readwell 프리뷰 설계: 읽을지 말지 먼저 판단하기

작성일: 2026-09-04
상태: 설계 확정, 구현 계획(plan) 착수 전
관련: [readwell 설계](./2026-07-11-readwell-design.md), [방법론](./methodology.md)

## 무엇을 만드나

글을 열기 전에 **읽을지 말지 판단할 재료**를 주는 단계를 하나 더 만든다.
지금 readwell은 "읽기로 이미 정한 글"을 잘 읽게 돕는다. 정하기 전 단계가 없다.

흐름을 둘로 나눈다.

- **훑어보기**: 프리뷰를 먼저 보고 판단한다. 읽기로 하면 거기서 본 읽기로 넘어간다.
- **바로 읽기**: 지금과 똑같다. 판단이 이미 끝난 글에 쓴다.

## 왜 요약이 아니라 프리뷰인가

readwell의 핵심 원칙은 "요약이 원문을 대체하지 않는다"다. 판단용 도구에서도 이 원칙이 그대로 걸린다.
오히려 더 세게 걸린다. 판단하려고 만든 요약이 결론까지 알려주면, 그걸 읽은 순간 원문을 읽을
이유가 사라진다. 판단을 돕는 게 아니라 판단을 없앤다.

문헌 요약 분야에는 이 구분에 이름이 있다.

- **informative(정보적) 요약**: 원문을 대체한다. 내용을 압축해 담는다. 흔한 TL;DR이 이쪽이다.
- **indicative(지시적) 요약**: 원문을 가리킨다. 무엇에 관한 글이고 어떤 종류의 주장을 하는지만
  알려주고 내용은 주지 않는다. 읽을지 말지 판단하는 용도로 설계된 장르다.

프리뷰는 indicative 쪽이다. 설계 원칙 한 줄로 줄이면 이렇다.
**내용을 주지 않고 성격을 준다.**

관련 이론 몇 가지를 배경으로 적어둔다.

- **Information Foraging** (Pirolli & Card 1999): 사람은 단서만 보고 그 뒤에 원하는 게
  있을지 추정한다. 이걸 information scent라고 부른다. 프리뷰는 scent를 제공하는 장치다.
  좋은 단서의 조건은 강한 게 아니라 정확한 것이다.
- **Relevance Theory** (Sperber & Wilson 1986): 관련성은 인지적 효과를 처리 노력으로 나눈 값이다.
  프리뷰가 노력을 줄여주는 만큼 예상 효과도 정직하게 알려줘야 판단이 선다.
- **매크로규칙** (Kintsch & van Dijk 1978): 축약은 삭제, 일반화, 구성이라는 연산이다.
  무엇을 버릴지가 설계 대상이지 부산물이 아니다.

이 문서의 프리뷰 필드 구성은 위 원칙에서 나왔다. 각 필드는 "이 글이 어떤 물건인가"에 답하고,
"이 글이 뭐라고 말하는가"에는 답하지 않는다.

## 전체 흐름

```
확장 팝업
 ├ [먼저 훑어보기] → POST /preview {url}         → 새 탭 GET /preview/{id}
 └ [이 페이지 읽기] → POST /read {url,questions}  → 새 탭 GET /view/{id}   (지금 그대로)

프리뷰 페이지
 └ [제대로 읽기] → POST /preview/{id}/read → 추출을 물려 세션 생성 → /view/{sid}로 이동
```

프리뷰도 10~20초 걸린다(추출 + LLM 1회). 확장 팝업은 닫히면 진행 중인 fetch가 죽으므로,
본 읽기와 똑같이 즉시 id를 돌려주고 작업은 서버가 이어서 하며 페이지가 폴링한다.

## 설계 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 프리뷰 대기 시간 | 10~20초, LLM 1회 | 추출만으로는 "어떤 종류의 주장인가"를 알 수 없어 판단 재료가 약하다 |
| 프리뷰 리소스 | 세션과 별개 (`/preview`) | 목록 오염이 구조적으로 불가능하고 정리가 디렉터리 단위로 끝난다 |
| 안 읽은 글 | 목록에 안 남김 | 판단만 하고 버린 글이 읽은 글 목록을 채우면 목록의 뜻이 흐려진다 |
| 승격 시 질문 | 프리셋 5개 | 버튼 하나로 끝나야 한다. 질문 타이핑은 훑어보기의 취지와 반대다 |
| 프리뷰 페이지의 원문 | 싣지 않는다 | 실으면 판단하러 왔다가 그냥 읽게 되어 흐름을 나눈 값어치가 없어진다 |

## 데이터 모델

`app/models.py`에 추가한다. 기존 `Session` 계열은 건드리지 않는다.

```python
PreviewStatus = Literal["extracting", "previewing", "done", "failed"]
PREVIEW_PENDING: frozenset[str] = frozenset({"extracting", "previewing"})


class PreviewQuote(_CamelModel):
    text: str              # 원문 그대로
    locator: str
    section: str | None    # 위치 감각용 섹션 제목


class Preview(_CamelModel):     # LLM이 채우는 부분
    about: str          # 무엇에 관한 글인가 (2~3문장, 주제와 범위)
    kind: str           # 튜토리얼 / 주장글 / 경험담 / 레퍼런스 / 뉴스
    claim_shape: str    # 주장의 모양. 결론 자체가 아니다
    evidence: str       # 벤치마크 / 저자 경험 / 인용 / 없음
    audience: str       # 대상 독자와 전제하는 배경지식
    not_covered: str    # 이 글이 다루지 않는 것
    quotes: list[PreviewQuote]

    @classmethod
    def json_schema(cls) -> dict: ...


class PreviewSession(_CamelModel):
    id: str
    url: str
    title: str
    created_at: str
    status: PreviewStatus       # extracting | previewing | done | failed
    error: str | None = None
    extraction: Extraction | None = None
    preview: Preview | None = None
    char_count: int = 0         # 추출에서 계산
    read_minutes: int = 0       # 추출에서 계산
    promoted_to: str | None = None   # 승격된 세션 id
```

`char_count`와 `read_minutes`는 LLM 스키마에서 뺀다. 모델에게 글자를 세라고 하면 틀리게 세고,
세지 않아도 알 수 있는 값이다.

`not_covered`가 판단에 특히 유용하다. 기대와 다른 글을 거기서 걸러낸다.

## 엔드포인트

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| POST | `/preview` | `{url}` → `{id, previewUrl, status}`. 추출·프리뷰는 백그라운드 |
| GET | `/preview/{id}` | 프리뷰 페이지 HTML. `promoted_to`가 있으면 `/view/{sid}`로 302 |
| GET | `/preview/{id}/status` | `{status, error}`. 페이지가 폴링한다 |
| POST | `/preview/{id}/read` | 추출을 물려 세션 생성 → `{id, viewUrl}` |

CORS 설정은 지금 것을 그대로 쓴다. `allow_methods=["POST"]`에 이미 걸린다.

## 저장과 정리

`data/previews/<id>.json`에 쓴다. `store.py`의 원자적 쓰기(임시 파일 + `os.replace`)만
`_atomic_write_json()` 헬퍼로 뽑아 세션과 공유하고, 프리뷰 함수는 따로 둔다.

- `save_preview` / `load_preview` / `list_preview_ids`
- `purge_previews(*, data_dir, older_than_days=7) -> int`

승격해도 프리뷰 파일은 지우지 않는다. `promoted_to`만 기록한다. 프리뷰 URL을 다시 열었을 때
404 대신 뷰로 보내기 위해서다. 서버 기동 시 `fail_stale_sessions` 옆에서 `purge_previews()`가
7일 지난 프리뷰를 승격 여부와 무관하게 지운다.

중단된 프리뷰(서버 재시작으로 `extracting`/`previewing`에 멈춘 것)도 기동 시 `failed`로 바꾼다.
안 그러면 페이지가 끝없이 폴링한다. 세션에 이미 있는 처리를 프리뷰에도 똑같이 건다.

목록(`/`)은 프리뷰를 아예 모른다. 다른 디렉터리라 필터링 코드가 필요 없다.

## 프리뷰 생성

`app/preview.py`를 새로 만든다. `reader.py`는 이미 261줄이고, 프리뷰는 원칙이 반대다.
같은 파일에 두면 "요약하지 마라"와 "성격만 말해라"가 서로 오염된다.

담을 것:

- `PREVIEW_SYSTEM_PROMPT`: 판단용 프리뷰의 제약. 핵심은 **답을 주지 마라, 이 글이 어떤
  물건인지만 알려줘라**. 결론·수치·해법을 옮기지 말고, 그런 게 있다는 사실만 알린다.
- `build_preview_prompt(extraction)`: 입력은 `reader.py`와 같은 XML 태그 관례를 따른다
  (`<title>`, `<source_url>`, `<body>`). 구분선을 쓰지 않는 이유는 README에 적힌 그대로다.
- `preview(extraction, *, agent) -> Preview`
- `estimate_reading(extraction) -> tuple[int, int]`: 글자수와 분. 한국어는 분당 500자,
  그 외는 분당 1000자로 잡는다. 언어 판정은 `translate.needs_translation`의 한글 비율을 쓴다.
- `verify_quotes(preview, extraction) -> Preview`: 아래 검증을 적용한 새 Preview를 돌려준다.

`about`, `claim_shape` 등 자유 서술 필드에는 `strip_prompt_echo()`를 건다.
`quotes[].text`는 원문 인용이므로 걸지 않는다. `reader.py`가 `answerQuote`를 다루는 것과 같다.

### 인용 검증

인용은 두 겹으로 검증한다.

1. `quotes[].text`가 원문 문단 안에 실제로 있는가. 공백을 정규화해 부분 문자열로 확인한다.
   없으면 지어낸 인용이므로 그 항목을 버린다.
2. `quotes[].locator`가 원문에 실재하는가. 실재하지 않으면 텍스트는 살리고 `locator`와
   `section`만 비운다. 인용문 자체가 값어치이고 위치는 부가다.

검증 결과 인용이 하나도 안 남으면 프리뷰는 그대로 살린다. 나머지 필드만으로도 판단은 된다.

### 긴 글

본문이 길면 프롬프트가 커진다. `READWELL_PREVIEW_MAX_CHARS`(기본 40000)를 넘으면
전문 대신 골격을 넣는다. 골격은 소제목 전부와 각 섹션 첫 문단이다. 대부분의 글은 앞머리에
논지를 세우므로 판단 재료로는 충분하다. 이 경우 인용이 각 섹션 앞부분에서만 나온다.

## 승격

`POST /preview/{id}/read`는 프리뷰의 `extraction`을 그대로 물려 새 `Session`을 만들고,
추출을 건너뛴 채 분석과 번역을 바로 시작한다. 질문은 프리셋 5개다.

`main.py`의 `_run`에서 추출 이후 부분을 떼어낸다.

```python
async def _analyze_and_translate(sid, extraction, questions) -> None:
    await asyncio.gather(
        _analyze_step(sid, extraction, questions),
        _translate_step(sid, extraction),
    )
```

`_run`은 추출한 뒤 이걸 부르고, 승격 경로는 추출 없이 바로 부른다.

승격 시:

- 새 세션은 `extraction`을 채운 채로 저장하고 `status`를 `analyzing`으로 시작한다.
  추출은 이미 끝났다. 저장을 먼저 해야 뷰가 열리자마자 원문을 보여준다.
- 프리뷰의 `title`, `url`(추출이 정규화한 값)을 그대로 쓴다.
- 프리뷰에 `promoted_to`를 기록한다.
- 이미 `promoted_to`가 있으면 새로 만들지 않고 기존 `viewUrl`을 돌려준다. 버튼 두 번 눌러도
  세션이 둘 생기지 않는다.
- 프리뷰가 `done`이 아니면 409를 돌려준다. 추출이 없으면 물려줄 게 없다.

## 프리뷰 페이지

뷰의 2단이 아니라 단일 컬럼 카드다. 판단용이니 한 화면에 들어와야 한다.

```
제목                                        원문 ↗
──────────────────────────────────────────────
  주장글 · 6,200자 · 약 8분

  무엇에 관한 글인가       ...2~3문장...
  어떤 주장을 하는가       ...
  근거                     ...
  누구를 위한 글인가       ...
  다루지 않는 것           ...

  저자의 문장
  " ... "                          「성능 측정」 섹션
  " ... "                          「결론」 섹션

  [제대로 읽기]
```

- 목록으로 가는 `←`는 두지 않는다. 프리뷰는 목록에 없는 물건이라 그리로 보내면 방금 본 게
  사라진 것처럼 보인다. 상단에는 제목과 원문 링크만 둔다.
- `app/templates/preview.html`, `app/static/preview.js`를 새로 만든다.
  CSS는 `view.css`에 프리뷰 블록을 덧붙인다. 색과 타이포를 따로 만들 이유가 없다.
- 인용의 위치는 링크가 아니라 섹션 이름으로 보여준다. 이 페이지에는 원문이 없어서 앵커가
  갈 곳이 없다. 승격 후 뷰에서는 locator가 원래대로 앵커로 동작한다.
- 폴링은 `view.js`와 같은 방식이다. `status`가 `done`이나 `failed`가 되면 멈춘다.
- `[제대로 읽기]`는 `POST /preview/{id}/read` 후 응답의 `viewUrl`로 이동한다.
  같은 탭에서 이동한다. 판단이 끝난 탭을 남겨둘 이유가 없다.
- 실패하면 사유와 원문 링크를 보여준다. 추출이 안 되는 사이트도 있다.

## 확장

팝업에 버튼을 하나 더 둔다.

```
[ 먼저 훑어보기 ]      ← 새 버튼. 질문칸을 무시한다
[ 이 페이지 읽기 ]     ← 지금 그대로
```

훑어보기가 위에 온다. 판단이 먼저 오는 순서다. 질문 입력칸은 그대로 두되, 훑어보기는 질문을
보내지 않는다. 프리뷰에 질문을 받으면 "번거로움 없이"라는 전제가 깨진다.

`manifest.json` 권한 변경은 없다. 같은 호스트다.

## 설정

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `READWELL_PREVIEW_MAX_CHARS` | 40000 | 넘으면 전문 대신 골격만 프리뷰에 넣는다 |
| `READWELL_PREVIEW_TTL_DAYS` | 7 | 이 기간 지난 프리뷰를 기동 시 지운다 |

`Config`에 두 필드를 추가한다.

## 테스트

`tests/test_preview.py`

- 프롬프트에 XML 태그와 locator가 붙는다
- 긴 본문이면 골격만 들어간다
- 스키마 검증 실패가 `PreviewError`가 된다
- 지어낸 인용이 버려진다
- 없는 locator는 텍스트만 남기고 위치가 비워진다
- 자유 서술 필드에서 프롬프트 반향이 걷힌다
- 한국어/영어 읽기 시간 계산

`tests/test_preview_api.py`

- `POST /preview` → 폴링 → `GET /preview/{id}`에 프리뷰가 뜬다
- 추출 실패가 `failed`와 사유로 남는다
- `POST /preview/{id}/read`가 세션을 만들고, **추출기가 다시 불리지 않는다**
  (가짜 추출기 호출 횟수로 확인)
- 두 번 승격해도 세션이 하나다
- 승격 후 `GET /preview/{id}`가 `/view/{sid}`로 302
- `done`이 아닌 프리뷰 승격은 409
- 목록(`/`)에 프리뷰가 안 나온다

`tests/test_store.py`에 프리뷰 저장·로드·정리를, `tests/test_extension_popup.py`에 버튼
두 개와 훑어보기가 질문을 안 보내는 것을 추가한다.

## 범위 밖

- 프리뷰 결과의 vault md 저장. 판단하고 버릴 물건이라 파일로 남길 이유가 없다.
- 프리뷰 인용문의 번역. 원문 문체를 보려고 넣은 것이라 번역하면 목적이 반쯤 사라진다.
  필요해지면 그때 붙인다.
- 프리뷰 단계의 추가 질문. 질문 없이 판단하는 게 이 기능의 전제다.
- 여러 URL 일괄 프리뷰.
