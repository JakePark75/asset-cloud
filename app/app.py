from shiny import App, ui, reactive, render, Outputs
from starlette.applications import Starlette
from starlette.routing import Mount

# app. 으로 시작하는 정석 경로
from app.context_api import routes as context_routes
from app.modules.dashboard import dashboard_ui, dashboard_server
from app.modules.portfolio import portfolio_ui, portfolio_server
from app.modules.accounts import accounts_ui, accounts_server
from app.modules.history import history_ui, history_server
from app.modules.settings import settings_ui, settings_server
from app.auth import verify_login, create_token, verify_token

app_ui = ui.page_fluid(
    ui.tags.meta(name="viewport", content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"),
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "base.css")),
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "dashboard.css")),
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "portfolio.css")),
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "accounts.css")),
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "history.css")),
    ui.tags.script(src="https://cdn.plot.ly/plotly-latest.min.js"),
    # 로그인 화면
    ui.div(
        ui.div(
            ui.h3("자산관리 시스템", style="text-align:center; margin-bottom:32px;"),
            ui.input_text("login_id", "아이디", placeholder="아이디 입력"),
            ui.input_password("login_pw", "비밀번호", placeholder="비밀번호 입력"),
            ui.input_checkbox("login_remember", "이 기기에서 30일간 유지", value=True),
            ui.input_action_button("login_btn", "로그인", class_="btn btn-primary w-100"),
            ui.output_text("login_error_msg"),
            style="max-width:320px; margin:0 auto; padding-top:120px;"
        ),
        id="screen-login",
        style="display:none;",
    ),

    # 메인 화면
    ui.div(
        ui.div(
            ui.div(dashboard_ui("dashboard"), id="tab-dashboard", class_="tab-content active"),
            ui.div(portfolio_ui("portfolio"), id="tab-portfolio", class_="tab-content"),
            ui.div(accounts_ui("accounts"), id="tab-accounts", class_="tab-content"),
            ui.div(history_ui("history"), id="tab-history", class_="tab-content"),
            ui.div(settings_ui("settings"), id="tab-settings", class_="tab-content"),
            style="padding-bottom: 70px;"
        ),
        ui.div(
            ui.div(ui.HTML("📊<br><span>대시보드</span>"), class_="tab-btn active", onclick="switchTab('dashboard', this)"),
            ui.div(ui.HTML("💼<br><span>포트폴리오</span>"), class_="tab-btn", onclick="switchTab('portfolio', this)"),
            ui.div(ui.HTML("🏦<br><span>계좌</span>"), class_="tab-btn", onclick="switchTab('accounts', this)"),
            ui.div(ui.HTML("📈<br><span>실적</span>"), class_="tab-btn", onclick="switchTab('history', this)"),
            ui.div(ui.HTML("⚙️<br><span>설정</span>"), class_="tab-btn", onclick="switchTab('settings', this)"),
            class_="bottom-tabbar"
        ),
        id="screen-main",
        style="display:none;"
    ),

    ui.tags.script("""
        // 탭 전환 + localStorage 저장
        function switchTab(name, el) {
            document.querySelectorAll('.tab-content').forEach(t => {
                t.style.display = 'none';
            });
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            var target = document.getElementById('tab-' + name);
            target.style.display = 'block';
            // IntersectionObserver 수동 트리거
            target.querySelectorAll('.shiny-bound-output').forEach(function(output) {
                const cb = $(output).data('shiny-intersection-observer-callback');
                if(cb) cb();
            });
            el.classList.add('active');
            localStorage.setItem('activeTab', name);
            Shiny.setInputValue('active_tab', name, {priority: 'event'});
        }
        // 페이지 로드 시 탭 복원
        function restoreTab() {
            var saved = localStorage.getItem('activeTab');
            if (!saved) return;
            var tabNames = ['dashboard', 'portfolio', 'accounts', 'history', 'settings'];
            var idx = tabNames.indexOf(saved);
            if (idx === -1) return;
            var content = document.getElementById('tab-' + saved);
            var btns = document.querySelectorAll('.tab-btn');
            if (!content || btns.length <= idx) return;
            document.querySelectorAll('.tab-content').forEach(t => {
                t.style.display = 'none';
            });
            btns.forEach(b => b.classList.remove('active'));
            content.style.display = 'block';
            content.querySelectorAll('.shiny-bound-output').forEach(function(output) {
                const cb = $(output).data('shiny-intersection-observer-callback');
                if(cb) cb();
            });
            btns[idx].classList.add('active');
            Shiny.setInputValue('active_tab', saved, {priority: 'event'});
        }
                   
        function showMain() {
            document.getElementById('screen-login').style.display = 'none';
            document.getElementById('screen-main').style.display = 'block';
            restoreTab();
        }

        function showLogin() {
            document.getElementById('screen-login').style.display = 'block';
            document.getElementById('screen-main').style.display = 'none';
        }

        // 쿠키 읽기
        function getCookie(name) {
            var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
            return match ? decodeURIComponent(match[1]) : null;
        }

        // 쿠키 저장 (days=0 이면 세션 쿠키 → 여기서는 미사용, remember=false 시 쿠키 저장 안 함)
        function setCookie(name, value, days) {
            var expires = '';
            if (days) {
                var d = new Date();
                d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
                expires = '; expires=' + d.toUTCString();
            }
            document.cookie = name + '=' + encodeURIComponent(value) + expires + '; path=/; SameSite=Strict';
        }

        function deleteCookie(name) {
            document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
        }

        // 페이지 로드 시 쿠키 확인 → Shiny로 전달
        $(document).on('shiny:sessioninitialized', function() {
            var token = getCookie('auth_token');
            if (!token) {
            showLogin();  // 쿠키 없으면 로그인 화면 표시
     }
            Shiny.setInputValue('cookie_token', token || '', {priority: 'event'});
        });

        // 서버에서 로그인 성공 시 → 쿠키 저장 + 메인 화면 전환
        Shiny.addCustomMessageHandler('login_success', function(msg) {
            if (msg.remember) {
                setCookie('auth_token', msg.token, 30);
            }
            showMain();
        });

        // 서버에서 쿠키 토큰 유효 확인 완료 시 → 메인 화면 전환
        Shiny.addCustomMessageHandler('token_valid', function(msg) {
            showMain();
        });

        // 서버에서 로그아웃 시
        Shiny.addCustomMessageHandler('logout', function(msg) {
            deleteCookie('auth_token');
            showLogin();
        });

        // Shiny 끊김 감지 시 reload
        $(document).on('shiny:disconnected', function() {
            location.reload();
        });

        // 백그라운드 진입 시 오버레이 씌우기 → reload 시 깜빡임 방지
        // 1. 오버레이를 생성하고 띄우는 건 '연결 끊김'이 감지될 때만!
        $(document).on('shiny:disconnected', function() {
            var overlay = document.getElementById('bg-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'bg-overlay';
                overlay.style.position = 'fixed';
                overlay.style.top = '0'; overlay.style.left = '0';
                overlay.style.width = '100%'; overlay.style.height = '100%';
                overlay.style.backgroundColor = 'black';
                overlay.style.zIndex = '9999';
                document.body.appendChild(overlay);
            }
            
            // 리로드 안전장치 (리로드가 발생할 때만 깜빡임 방지용)
            setTimeout(function() {
                location.reload();
            }, 50); // 50ms면 리로드 명령 내리기엔 충분합니다
        });
    """),
)

def server(input, output, session):
    from app.price_signal import start_signal_listener
    from app.db import get_config
    start_signal_listener()

    active_tab = reactive.value("dashboard")

    @reactive.effect
    @reactive.event(input.active_tab)
    def _sync_active_tab():
        active_tab.set(input.active_tab())

    dashboard_server("dashboard", active_tab=active_tab)
    portfolio_server("portfolio", active_tab=active_tab)
    accounts_server("accounts", active_tab=active_tab)
    history_server("history", active_tab=active_tab)
    settings_server("settings", active_tab=active_tab)

    # 페이지 로드 시 쿠키 토큰 검증
    @reactive.effect
    @reactive.event(input.cookie_token)
    async def _():
        token = input.cookie_token()
        if not token:
            return
        if verify_token(token):
            try:
                await session.send_custom_message("token_valid", {})
            except Exception:
                pass

    # 로그인 버튼 클릭 처리
    @reactive.effect
    @reactive.event(input.login_btn)
    async def _():
        login_id = input.login_id()
        login_pw = input.login_pw()
        remember = input.login_remember()

        if verify_login(login_id, login_pw):
            token = create_token(remember=remember)
            try:
                await session.send_custom_message("login_success", {
                    "token": token,
                    "remember": remember
                })
            except Exception:
                pass
        else:
            login_error.set("아이디 또는 비밀번호가 올바르지 않습니다.")

    login_error = reactive.value("")

    @render.text
    def login_error_msg():
        return login_error.get()

shiny_app = App(app_ui, server)

routes = context_routes + [
    Mount("/", app=shiny_app),
]

app = Starlette(routes=routes)