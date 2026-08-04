# readwell 브라우저 확장 (MV3)

현재 탭 URL을 readwell 웹서비스로 보내고, 원문+분석 뷰를 새 탭으로 연다.

Chrome과 Firefox(128+) 양쪽에서 같은 소스로 동작한다.

## 읽기 전 질문

팝업의 입력칸에 이 글에서 알고 싶은 것을 **한 줄에 하나씩** 적고 읽기를 누른다.
그러면 기본 질문 5개 대신 내 질문의 답을 원문 인용으로 받는다. 비워두면 기본 질문으로
읽는다. 읽기 전에 질문이 안 떠오르면 비우고, 뷰의 추가질문 탭에서 물어도 된다.

## 설치 (Chrome)

1. `chrome://extensions` 열기
2. 우측 상단 **개발자 모드** 켜기
3. **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더 선택
4. 툴바의 readwell 아이콘 클릭 → **이 페이지 읽기**

`browser_specific_settings` 키를 모른다며 경고가 뜨는데, Firefox 전용 키라 Chrome에서는 무시된다.

## 설치 (Firefox)

1. `about:debugging#/runtime/this-firefox` 열기
2. **임시 부가 기능 로드** → 이 폴더의 `manifest.json` 선택 (폴더가 아니라 파일이다)
3. 툴바의 readwell 아이콘 클릭 → **이 페이지 읽기**

임시 로드는 브라우저를 끄면 사라진다. 계속 쓰려면 `web-ext build`로 zip을 만들어
서명(`web-ext sign`)하거나, `about:config`에서 `xpinstall.signatures.required`를 끌 수 있는
Developer Edition / Nightly를 쓴다.

Firefox는 임시 로드한 MV3 확장에 호스트 권한을 자동으로 주지 않는다. 설치 프롬프트를
거치지 않아서다. 권한을 주려면 `about:addons` → readwell → **권한**에서 사이트 접근을
켠다. 권한이 없으면 확장 요청도 일반 웹페이지처럼 CORS를 타는데, 서버가 확장 오리진의
프리플라이트를 허용하므로 권한 없이도 동작은 한다.

## 서버 주소

- 기본값: `http://localhost:2100` (로컬 실행)
- 원격은 `http://100.99.117.44:2100` (Tailscale로 hops-dev-2에 닿는 주소)
- 바꾸려면 팝업의 **서버 주소 설정**에서 endpoint 입력.
- 호스트를 바꾸면 `manifest.json`의 `host_permissions`에도 추가해야 fetch가 허용된다.

**`host_permissions`에 포트를 쓰지 말 것.** Firefox는 매치 패턴의 포트를 지원하지
않아서, `http://localhost:2100/*`처럼 쓰면 에러 없이 어떤 URL에도 매치되지 않는다.
호스트 권한이 사라진 상태로 요청이 나가고, `POST /read`는 `application/json`이라
프리플라이트가 붙어서 CORS에 걸린다. 증상은 `NetworkError when attempting to fetch
resource.`다. 포트를 빼면 그 호스트의 모든 포트에 매치된다.
(Bugzilla [1362809](https://bugzilla.mozilla.org/show_bug.cgi?id=1362809),
[1468162](https://bugzilla.mozilla.org/show_bug.cgi?id=1468162))

이 규칙은 `tests/test_extension_manifest.py`가 지킨다.

## 동작

1. 팝업 버튼 클릭 → 현재 탭 `url`을 `POST {endpoint}/read`로 전송
2. 서버는 세션만 만들고 `viewUrl`을 **즉시** 돌려준다. 추출·분석은 서버가 이어서 한다
3. 확장은 그 URL을 새 탭으로 열고 팝업은 닫힌다
4. 뷰 페이지가 `GET /view/{id}/status`를 2초마다 폴링하다 상태가 바뀌면 새로고침한다
   - 추출이 끝나면 왼쪽 원문부터 뜬다. 분석을 기다리며 바로 읽을 수 있다
   - 분석이 끝나면 오른쪽이 채워진다
5. 분석이 실패해도 추출 결과로 뷰는 열린다

팝업은 닫히면 문서가 파괴돼 진행 중인 `fetch`도 같이 죽는다. 그래서 확장이 결과를
기다리지 않고 서버가 작업을 맡는다. 기다리는 동안 다른 탭을 봐도 된다.

## 범위

- v1은 URL만 보낸다. 로그인/페이월 뒤 콘텐츠의 DOM 본문 전송은 v2.
- 개인용. 대량 호출 금지(구독 사용량 한도).
