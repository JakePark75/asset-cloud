# app.py — 구조 요약

### 역할
- Shiny App + Starlette로 감싸서 실행
- 5개 탭 모듈 등록 및 라우팅
- 하단 탭바 JS(`switchTab`)로 탭 전환 (CSS show/hide 방식)
- 로그인/인증 처리 (`auth.py` 연동)

### UI 구조
- `ui.page_fluid` 안에 로그인 화면(`screen-login`) + 메인 화면(`screen-main`) 분리
- 초기 둘 다 `display:none`, JS에서 쿠키 확인 후 표시 결정
- 각 탭: dashboard / portfolio / accounts / history / settings
- 탭 전환: `switchTab(name, el)` JS 함수
- 탭 상태: localStorage에 저장, 페이지 로드 시 복원
- Shiny 끊김 감지: `shiny:disconnected` 이벤트 → `location.reload()`
- 콘텐츠 영역 하단 여백: `padding-bottom: 70px` (하단 탭바 높이)

### 로그인 구현
- 인증 로직: `auth.py`의 `verify_login`, `create_token`, `verify_token` 사용
- 쿠키 기반 세션 유지
- 로그인 화면: 아이디/비밀번호 입력 + "이 기기에서 30일간 유지" 체크박스 (기본 체크)
- 체크 시: 쿠키 만료 30일, 미체크 시: 쿠키 저장 안 함 (세션 유지 없음)
- 쿠키 읽기/쓰기/삭제: JS (`getCookie`, `setCookie`, `deleteCookie`)
- 페이지 로드 시 (`shiny:sessioninitialized`): 쿠키 토큰을 `cookie_token` input으로 Shiny에 전달
- 토큰 유효 시: 서버에서 `token_valid` 메시지 → JS `showMain()`
- 로그인 성공 시: 서버에서 `login_success` 메시지 → JS 쿠키 저장 + `showMain()`
- 로그아웃: 서버에서 `logout` 메시지 → JS 쿠키 삭제 + `showLogin()`

### Server 구조
- 각 모듈 server 함수를 네임스페이스로 등록
- `dashboard_server` / `portfolio_server` / `accounts_server` / `history_server` / `settings_server`
- `cookie_token` input 감지 → `verify_token()` 검증 → `token_valid` 메시지 전송
- `login_btn` 클릭 → `verify_login()` 검증 → 성공 시 `login_success` / 실패 시 오류 메시지 표시

### 라우팅
- Starlette로 감싸서 실행
- context_api routes + Shiny 앱 `Mount("/")`