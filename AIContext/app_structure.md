# app.py — 구조 요약

### 역할
- Shiny App + Starlette로 감싸서 실행
- 5개 탭 모듈 등록 및 라우팅
- 하단 탭바 JS(switchTab)로 탭 전환 (CSS show/hide 방식)

### UI 구조
- `ui.page_fluid` 안에 탭별 div (tab-content) + 하단 탭바
- 각 탭: dashboard / portfolio / accounts / history / settings
- 탭 전환: `switchTab(name, el)` JS 함수
- 탭 상태: localStorage에 저장, 페이지 로드 시 복원
- Shiny 끊김 감지: `shiny:disconnected` 이벤트 → `location.reload()`
- 콘텐츠 영역 하단 여백: `padding-bottom: 70px` (하단 탭바 높이)

### Server 구조
- 각 모듈 server 함수를 네임스페이스로 등록
- dashboard_server / portfolio_server / accounts_server / history_server / settings_server

### 라우팅
- Starlette로 감싸서 실행
- context_api routes + Shiny 앱 Mount("/")

### 로그인 구현 예정
- 인증 정보: config.json의 login_id + db_password 사용
- 쿠키 기반 세션 유지
- 로그인 화면: 아이디/비밀번호 입력 + "이 기기에서 30일간 유지" 체크박스 (기본 체크)
- 체크 시: 쿠키 만료 30일, 미체크 시: 세션 쿠키
- 쿠키 유효하면 로그인 화면 스킵, 바로 메인 화면
- 쿠키 읽기/쓰기는 JS로 처리, Shiny input으로 값 전달
- 로그인 성공/실패 처리는 Shiny server에서 검증
- 구현 위치: app.py 또는 별도 auth.py 모듈