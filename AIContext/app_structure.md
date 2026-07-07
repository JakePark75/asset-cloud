Module: App Main Core (메인 엔트리 및 인증) — 구조 명세서
1. 아키텍처 및 시스템 런타임 환경
엔트리 포인트: app.py는 Shiny App 인스턴스를 생성하고 Starlette ASGI 비동기 애플리케이션 프레임워크로 감싸서 실행되는 시스템의 최상위 진입점입니다.
라우팅 통합: 단일 포트 포워딩 및 컨텍스트 연동을 위해 app.context_api와 app.export_api에 설계된 REST API 라우트 체계(routes)를 Starlette(routes=...) 단에 함께 마운트하여 웹 소켓 통신과 일반 HTTP 인터페이스를 동시 처리합니다.
탭 전환 아키텍처 (SPA): Shiny 고유의 다중 탭 레이아웃 렌더러를 사용하지 않고, 성능 최적화(DOM 파괴 방지)를 위해 단일 페이지 애플리케이션(SPA) 스타일의 CSS show/hide 방식을 채택했습니다.
백엔드에 빈번한 렌더링 요청을 보내지 않고, 하단 고정 탭바 클릭 시 프론트엔드 내장 JS 함수인 switchTab(tabName, el)이 각 모듈 컨테이너의 display: none / block 속성을 직접 스위칭합니다.
세션 유실 방지: 모바일 환경 등에서 흔히 발생하는 Shiny 소켓 단선 에러를 복구하기 위해, shiny:disconnected 이벤트를 하이재킹하여 브라우저 수준에서 자동으로 window.location.replace(window.location.href)를 실행시키는 자동 재접속 훅이 상주합니다.

2. 파일 구성 및 모듈 인젝션 관계
app.py: 최상위 UI 레이아웃 선언 및 3대 최상위 화면 서버 모듈 런타임 활성화
app/auth.py: JWT 및 암호화 알고리즘 기반 토큰 생성/검증 처리 레이어
app/context_api.py: 외부 에이전트 인터페이스용 REST 엔드포인트 라우팅 매핑
app/export_api.py: 외부 내보내기 기능용 REST 엔드포인트 라우팅 매핑
app/static/base.css: 전역 다크 테마 공통 변수, 하단 고정 탭바 및 로그인 스크린 레이아웃 스타일시트

3. 데이터 흐름 및 상태 관리 (Data Lifecycle)
3.1 전역 Reactive State 관리
active_tab: reactive.value("asset") -> JS 레이어의 탭 전환 동작과 백엔드 서버 모듈의 연동을 위해 현재 활성화된 최상위 탭명을 문자열 상태로 동기화합니다.
페이지가 최초 로드된 뒤 쿠키 토큰이 있으면 메인 화면을 표시하면서 JS가 브라우저 내 localStorage에서 유저가 마지막으로 머물렀던 탭 상태를 조회하여 백엔드의 input.active_tab 공간으로 복원 신호를 전송합니다.
3.2 서브 모듈 호출 파이프라인
app.py는 레이아웃 계층 내에 3대 최상위 탭 UI를 병렬 로드한 후, 내부 server 함수에서 각 모듈의 독립 서버를 인스턴스화합니다.
이때 의존성 주입(DI) 형태로 전역 상태인 active_tab 참조를 넘겨주어, 개별 모듈들이 "현재 내가 화면에 노출된 상태인가"를 인지하고 불필요한 차트 연산이나 DOM 패치 메시지 송신을 스스로 억제할 수 있도록 제어합니다.
# app.py 내 모듈 서버 가동 파이프라인 규격
asset_server("asset", active_tab=active_tab)
history_server("history", active_tab=active_tab)
settings_server("settings", active_tab=active_tab)



4. 쿠키 기반 사용자 인증 매커니즘 (Security & Auth)
화면 격리 체계: 시스템은 다크 테마 보호 및 개인 자산 보안을 위해 이중 화면 격리 구조(screen-login, screen-main)를 가집니다.
초기 상태: 페이지 초기 진입 시 두 영역 모두 display: none 상태이며, 아래의 쿠키 검증 절차 결과에 따라 최종 노출 레이어가 결정됩니다.
4.1 로그인 성공 및 세션 유지 흐름
사용자 액션: 로그인 버튼 클릭 시 백엔드의 @reactive.event(input.login_btn) 이벤트가 트리거됩니다.
자격 증명 검증: auth.py의 verify_login(id, pw) 함수를 호출하여 일치 여부를 판단한 뒤, 검증에 성공하면 create_token(remember)을 실행합니다.
만료일 제어 (Cookie Lifecycle):
"이 기기에서 30일간 유지" (input.login_remember) 체크박스 활성화시: create_token(remember=True)가 30일(exp)짜리 JWT를 발행하고, 프론트엔드가 받은 토큰을 auth_token 쿠키에 30일(max-age=2592000)로 저장합니다.
비활성화시: create_token(remember=False)가 12시간(exp)짜리 JWT를 발행하지만, 현재 JS는 remember=true일 때만 쿠키를 저장하므로 브라우저 쿠키로는 지속되지 않습니다.
화면 전환 신호: 백엔드는 즉시 send_custom_message("login_success", {"token": ..., "remember": ...}) 메시지를 브라우저로 전송합니다. 프론트엔드 JS 레이어가 이를 받아 remember=true인 경우에만 쿠키를 로컬에 저장한 뒤, 로그인 창을 숨기고 메인 화면을 표시합니다.
4.2 페이지 재진입 및 자동 로그인 흐름 (Initialization)
쿠키 룩업: 브라우저 로딩 완료 시점(shiny:sessioninitialized 이벤트 발생 단계)에 JS가 로컬 브라우저 쿠키 저장소에서 기존 토큰 문자열이 존재하는지 검사합니다. 토큰이 없으면 로그인 화면을 그대로 유지합니다.
백엔드 토스: 토큰이 발견되면 가상 입력 채널인 Shiny.setInputValue('cookie_token', token)을 통해 백엔드로 토큰 세션을 전달합니다.
무결성 검증: 서버의 @reactive.event(input.cookie_token) 감시자가 작동하여 verify_token(token) 모듈로 유효성을 검증합니다.
자동 로그인 승인: 변조되지 않은 토큰임이 확인되면 클라이언트에 send_custom_message("token_valid", {}) 성공 메시지를 회신하여, 프론트엔드가 로그인 화면을 패스하고 메인 자산 관리 영역으로 직행하도록 제어합니다.

5. 프론트-백엔드 전역 인터페이스 규격
5.1 커스텀 메시지 핸들러 (서버 ➔ 클라이언트)
핸들러 이름
데이터 Payload 구조
프론트엔드 DOM / 쿠키 액션 수행 규격
login_success
{"token": str, "remember": bool}
브라우저 쿠키 저장소에 보안 토큰을 기록하고 screen-login 컨테이너를 즉시 숨긴 뒤 screen-main 컨테이너를 오픈
token_valid
{}
로그인 대기 상태를 해제하고 즉시 screen-main 자산 관리 뷰 포트를 유저에게 노출


5.2 글로벌 매핑 전역 DOM ID
DOM ID
컴포넌트 유형
시스템 내 역할 및 바인딩 목적
screen-login
Container Div
비인증 상태의 사용자가 마주하는 로그인 레이아웃 최상위 컨테이너
screen-main
Container Div
전체 자산 관리 영역 및 최상위 탭 컴포넌트가 상주하는 메인 레이아웃 최상위 컨테이너
login_id
Input Text
사용자 인증용 ID 입력 필드
login_pw
Input Password
사용자 인증용 패스워드 입력 필드 (마스킹 처리)
login_remember
Input Checkbox
30일간 세션 유지를 결정하는 체크박스 컴포넌트
login_error_msg
Output Text
로그인 인증 실패 시 백엔드가 반환하는 예외 텍스트 출력 레이블 (@render.text)


