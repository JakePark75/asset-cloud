from shiny import App, ui
from starlette.applications import Starlette
from starlette.routing import Mount

from context_api import routes as context_routes
from modules.dashboard import dashboard_ui, dashboard_server
from modules.portfolio import portfolio_ui, portfolio_server
from modules.accounts import accounts_ui, accounts_server
from modules.history import history_ui, history_server
from modules.settings import settings_ui, settings_server

app_ui = ui.page_fluid(
    ui.include_css(str(__import__("pathlib").Path(__file__).parent / "static" / "style.css")),
    # 각 화면 (탭별로 show/hide)
    ui.div(
        ui.div(dashboard_ui("dashboard"), id="tab-dashboard", class_="tab-content active"),
        ui.div(portfolio_ui("portfolio"), id="tab-portfolio", class_="tab-content"),
        ui.div(accounts_ui("accounts"), id="tab-accounts", class_="tab-content"),
        ui.div(history_ui("history"), id="tab-history", class_="tab-content"),
        ui.div(settings_ui("settings"), id="tab-settings", class_="tab-content"),
        style="padding-bottom: 70px;"  # 하단 탭바 높이만큼 여백
    ),
    # 하단 탭바
    ui.div(
        ui.div(ui.HTML("📊<br><span>대시보드</span>"), class_="tab-btn active", onclick="switchTab('dashboard', this)"),
        ui.div(ui.HTML("💼<br><span>포트폴리오</span>"), class_="tab-btn", onclick="switchTab('portfolio', this)"),
        ui.div(ui.HTML("🏦<br><span>계좌</span>"), class_="tab-btn", onclick="switchTab('accounts', this)"),
        ui.div(ui.HTML("📈<br><span>실적</span>"), class_="tab-btn", onclick="switchTab('history', this)"),
        ui.div(ui.HTML("⚙️<br><span>설정</span>"), class_="tab-btn", onclick="switchTab('settings', this)"),
        class_="bottom-tabbar"
    ),
    ui.tags.script("""
        // 탭 전환 + localStorage 저장
        function switchTab(name, el) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-' + name).classList.add('active');
            el.classList.add('active');
            localStorage.setItem('activeTab', name);
        }

        // 페이지 로드 시 탭 복원
        (function() {
            var saved = localStorage.getItem('activeTab');
            if (!saved) return;
            var tabNames = ['dashboard', 'portfolio', 'accounts', 'history', 'settings'];
            var idx = tabNames.indexOf(saved);
            if (idx === -1) return;
            var content = document.getElementById('tab-' + saved);
            var btns = document.querySelectorAll('.tab-btn');
            if (!content || btns.length <= idx) return;
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            btns.forEach(b => b.classList.remove('active'));
            content.classList.add('active');
            btns[idx].classList.add('active');
        })();

        // Shiny 끊김 감지 시 reload
        $(document).on('shiny:disconnected', function() {
            location.reload();
        });
    """),

)

def server(input, output, session):
    dashboard_server("dashboard")
    portfolio_server("portfolio")
    accounts_server("accounts")
    history_server("history")
    settings_server("settings")

shiny_app = App(app_ui, server)

routes = context_routes + [
    Mount("/", app=shiny_app),
]

app = Starlette(routes=routes)
