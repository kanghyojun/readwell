# readwell 브라우저 확장 (MV3)

현재 탭 URL을 readwell 웹서비스로 보내고, 원문+분석 뷰를 새 탭으로 연다.

## 설치 (Mac Chrome)

1. `chrome://extensions` 열기
2. 우측 상단 **개발자 모드** 켜기
3. **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더 선택
4. 툴바의 readwell 아이콘 클릭 → **이 페이지 읽기**

## 서버 주소

- 기본값: `http://100.99.117.44:2100` (Tailscale로 hops-dev-2에 닿는 주소)
- 바꾸려면 팝업의 **서버 주소 설정**에서 endpoint 입력.
- 주소/포트를 바꾸면 `manifest.json`의 `host_permissions`도 같은 주소로 맞춰야 fetch가 허용된다.

## 동작

1. 팝업 버튼 클릭 → 현재 탭 `url`을 `POST {endpoint}/read`로 전송
2. 응답의 `viewUrl`(없으면 `{endpoint}/view/{id}`)을 새 탭으로 연다
3. 분석이 일부 실패해도 추출 결과로 뷰는 열린다

## 범위

- v1은 URL만 보낸다. 로그인/페이월 뒤 콘텐츠의 DOM 본문 전송은 v2.
- 개인용. 대량 호출 금지(구독 사용량 한도).
