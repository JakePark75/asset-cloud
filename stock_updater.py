"""
KIS API 주식 데이터 자동 업데이터
- 시스템 트레이 아이콘으로 백그라운드 실행 (기존 기능 유지)
- 지정된 엑셀 파일 자동 오픈
- 1분마다 KIS API 호출하여 열려있는 엑셀 시트에 직접 업데이트
- 텔레그램 메시지 수정 방식을 사용하여 대화방을 깔끔하게 유지 (알림 스트레스 없음)
- 행별 컬럼 위치 미세 조정(offsets) 기능 포함
"""

import json
import os
import sys
import time
import logging
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

import urllib3
import requests
import win32com.client
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw,ImageGrab,ImageFont

config = {}
update_event        = threading.Event()  # 즉시 업데이트 신호탄
historical_mode_evt = threading.Event()  # 과거 데이터 조회 모드 플래그
tray_icon           = None               # 트레이 아이콘 전역 참조
work_stop_evt       = threading.Event()  # 현재 작업 중단 신호
work_stopped_evt    = threading.Event()  # 작업 조기중단 완료 알림
is_in_office = False   # 사무실 모드 플래그

# 사내 네트워크 SSL 경고 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE    = BASE_DIR / "stock_updater.log"
STATE_FILE  = BASE_DIR / "state.json"

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── 전역 상태 ─────────────────────────────────────────────────────────────────
status = {
    "last_update": "아직 없음",
    "last_status": "시작 중...",
    "error": False,
    "last_heartbeat": datetime.now(),
    "last_telegram_msg_id": None,  # 마지막으로 전송된 텔레그램 메시지 ID 저장
}
access_token  = None
token_expires = 0
config        = {}

# ── 유틸리티 함수 ─────────────────────────────────────────────────────────────
def get_visual_width(text):
    """한글 2칸, 영문/숫자 1칸으로 계산하여 실제 시각적 너비 반환"""
    if not text: return 0
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)

def escape_md(text):
    """MarkdownV2 특수문자 이스케이프 강화"""
    if not text: return ""
    # MarkdownV2에서 이스케이프가 필요한 모든 특수문자 목록
    special_chars = r'_*[]()~`>#+-=|{}.!'
    for c in special_chars:
        text = text.replace(c, f"\\{c}")
    return text

def load_config():
    if not CONFIG_FILE.exists():
        log.error(f"config.json 파일을 찾을 수 없습니다: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("interval", 3)
    return cfg

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    except Exception as e:
        log.warning(f"config 저장 실패: {e}")
        
def load_state():
    global status

    if not STATE_FILE.exists():
        return

    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)

        status["last_telegram_msg_id"] = data.get("last_telegram_msg_id")

    except Exception as e:
        log.warning(f"state 로드 실패: {e}")


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "last_telegram_msg_id": status.get("last_telegram_msg_id")
            }, f)

    except Exception as e:
        log.warning(f"state 저장 실패: {e}")
        
# ── 엑셀 및 API 관련 ──────────────────────────────────────────────────────────
def open_excel_and_get_sheet():
    excel_path = str(Path(config["excel_file"]).resolve())
    sheet_name = config["sheet_name"]
    file_name  = Path(excel_path).name

    try:
        xl = win32com.client.GetActiveObject("Excel.Application")
    except:
        xl = win32com.client.Dispatch("Excel.Application")
    xl.Visible = True

    wb = None
    for book in xl.Workbooks:
        if book.Name == file_name:
            wb = book
            log.info(f"이미 열려있는 파일 사용: {file_name}")
            break

    if wb is None:
        log.info(f"엑셀 파일 오픈 중: {excel_path}")
        wb = xl.Workbooks.Open(excel_path)

    ws = wb.Sheets(sheet_name)
    return xl, wb, ws

def find_columns(ws):
    header_row = None
    col_ticker = col_price = col_change = col_time = col_div = col_data_time = None
    for r in range(1, 6):
        for c in range(1, 20):
            val = ws.Cells(r, c).Value
            if val in ("티커", "Ticker", "ticker"):
                header_row = r
                col_ticker = c
            elif val in ("현재가", "Price", "price"):
                col_price = c
            elif val in ("등락률", "Change%", "change%", "등락율"):
                col_change = c
            elif val in ("업데이트시간", "업데이트 시간", "Updated"):
                col_time = c
            elif val in ("구분", "Type", "type"):
                col_div = c
            elif val in ("데이터시간", "data_time"):
                col_data_time = c
    if not header_row or not col_ticker:
        raise ValueError("시트에서 티커 헤더를 찾을 수 없습니다.")
    return header_row, col_ticker, col_price, col_change, col_time, col_div, col_data_time

# ── 데이터 수집 함수들 ────────────────────────────────────────────────────────
def get_access_token():
    global access_token, token_expires
    if access_token and time.time() < token_expires:
        return access_token
    url  = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": config["app_key"], "appsecret": config["app_secret"]}
    res  = requests.post(url, json=body, timeout=10, verify=False)
    res.raise_for_status()
    data = res.json()
    access_token  = data["access_token"]
    token_expires = time.time() + int(data.get("expires_in", 86400)) - 60
    return access_token

def get_kr_price(ticker):
    token = get_access_token()
    url   = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {token}", "appkey": config["app_key"], "appsecret": config["app_secret"], "tr_id": "FHKST01010100", "custtype": "P"}
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
    res    = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    out    = res.json().get("output", {})
    curr_price = float(out.get("stck_prpr", 0))
    price      = curr_price if curr_price != 0 else float(out.get("prdy_clpr", 0))
    return price, float(out.get("prdy_ctrt", 0))

def get_us_price(ticker, excd):
    token = get_access_token()
    url = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/price"
    headers = {"authorization": f"Bearer {token}", "appkey": config["app_key"], "appsecret": config["app_secret"], "tr_id": "HHDFS00000300", "custtype": "P"}
    params = {"AUTH": "", "EXCD": excd, "SYMB": ticker}
    res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    out = res.json().get("output", {})
    def safe_float(v):
        try: return float(v) if v not in ("", None) else 0
        except: return 0
    price = safe_float(out.get("last")) if safe_float(out.get("last")) != 0 else safe_float(out.get("base"))
    return price, safe_float(out.get("rate"))

def get_fx_rate(ticker):
    if not ticker or "/" not in ticker: return None, None
    base_currency, target_currency = ticker.upper().split("/")
    url = f"https://open.er-api.com/v6/latest/{base_currency}"
    data = requests.get(url, verify=False).json()
    return data['rates'].get(target_currency), 0

def get_index_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
    meta = res["chart"]["result"][0]["meta"]
    price = float(meta.get("regularMarketPrice", 0))
    prev_close = float(meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0))
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
    return price, change_pct, datetime.fromtimestamp(meta.get("regularMarketTime", 0))

# ── 텔레그램 및 리포트 ────────────────────────────────────────────────────────
def delete_telegram_message():
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")
    msg_id = status.get("last_telegram_msg_id")

    if not token or not chat_id or not msg_id:
        return

    url = f"https://api.telegram.org/bot{token}/deleteMessage"

    try:
        res = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "message_id": msg_id
            },
            timeout=10,
            verify=False
        ).json()

        if res.get("ok"):
            log.info(f"기존 텔레그램 메시지 삭제 완료 ({msg_id})")

    except Exception as e:
        log.warning(f"메시지 삭제 실패: {e}")
        
def send_telegram_msg(message):

    global status

    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id:
        log.warning("텔레그램 토큰이나 채팅 ID가 설정되지 않았습니다.")
        return

    delete_telegram_message()

    send_url = f"https://api.telegram.org/bot{token}/sendMessage"

    params = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_notification": True
    }

    try:
        res = requests.post(
            send_url,
            data=params,
            timeout=10,
            verify=False
        ).json()

        if res.get("ok"):

            status["last_telegram_msg_id"] = res["result"]["message_id"]

            save_state()

            log.info(
                f"새 텔레그램 메시지 전송 완료 "
                f"(ID: {status['last_telegram_msg_id']})"
            )

        else:
            log.error(f"메시지 전송 실패: {res.get('description')}")

    except Exception as e:
        log.error(f"메시지 전송 API 호출 중 예외 발생: {e}")
        
def send_status_report(ws_inst):
    global config, log

    try:
        now_str = datetime.now().strftime('%m월%d일 %H:%M')

        # 1. 이미지 전송 우선 시도
        try:
            img_path = BASE_DIR / "report.png"

            create_report_image(
                ws_inst,
                img_path
            )

            if is_in_office:#사무실이면 이미지 생성만 하고 끝
                log.info(f"사무실 모드, 메세지 전송 안함")
            else:
                send_telegram_photo(img_path, f"업데이트: {now_str}")
            return

        except Exception as e:
            log.warning(f"이미지 생성 실패 → 텍스트 전송 사용: {e}")

        # 2. fallback 텍스트 생성
        target_range = ws_inst.Range("A1").Value

        if not target_range:
            return

        data = ws_inst.Range(target_range).Value

        report_text = f"📊 *자산 실시간 분석* {escape_md('(' + now_str + ')')}\n"
        report_text += "```\n"

        BASE_START = 12
        TOTAL_WIDTH = 26
        offsets = [0, 1, 0, 0, 0, -2, -2, 0, 0, 0, 0]

        data_row_idx = 0

        for row in data:

            label = str(row[0]) if row[0] is not None else ""
            val = row[1] if row[1] is not None else ""

            if label.strip() in ("-", "- -"):
                report_text += "-" * TOTAL_WIDTH + "\n"
                continue

            val_str = str(val) if val else "0"

            current_offset = (
                offsets[data_row_idx]
                if data_row_idx < len(offsets)
                else 0
            )

            column_2_pos = BASE_START + current_offset

            label_w = get_visual_width(label)

            pad_count = max(1, column_2_pos - label_w)

            spaces = " " * pad_count

            line = f"{label}{spaces}{val_str}\n"

            report_text += escape_md(line)

            data_row_idx += 1

        report_text += "```\n"

        report_text += (
            f"🔄 _"
            f"{escape_md('업데이트 주기: ' + str(config.get('interval', 1)) + '분')}"
            f"_"
        )

        send_telegram_msg(report_text)

    except Exception as e:
        log.error(f"리포트 생성 오류: {e}")
# ──
def draw_card(draw, x1, y1, x2, y2):

    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=28,
        fill=(20, 22, 28),
        outline=(42, 45, 54),
        width=2
    )

import re
import io
import base64
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# ── [변경] 외부 아이콘 로드 헬퍼 함수 ──────────────────
def _get_icon_image_from_config(config_key):
    """config에 등록된 경로에서 이미지를 로드하고 없으면 투명한 임시 이미지 반환"""
    icon_path = config.get(config_key)
    if icon_path and os.path.exists(icon_path):
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception as e:
            log.warning(f"아이콘 로드 실패 ({config_key}: {icon_path}): {e}")
    
    # 파일이 없거나 에러 발생 시 프로그램 중단을 막기 위한 빈 투명 이미지 생성
    return Image.new("RGBA", (100, 100), (0, 0, 0, 0))

# ── [변경] 외부 아이콘 로드 헬퍼 함수 (함수 위에 배치) ──────────────────
def _get_icon_image_from_config(config_key):
    """config에 등록된 경로에서 이미지를 로드하고 없으면 투명한 임시 이미지 반환"""
    icon_path = config.get(config_key)
    if icon_path and os.path.exists(icon_path):
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception as e:
            log.warning(f"아이콘 로드 실패 ({config_key}: {icon_path}): {e}")
    return Image.new("RGBA", (100, 100), (0, 0, 0, 0))

"""
create_report_image() 교체용 함수
기존 소스에서 이 함수만 통째로 교체하세요.
의존: PIL (Pillow), math, re
폰트: malgun.ttf, malgunbd.ttf (기존과 동일)
"""

import math
import re
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime


def create_report_image(ws_inst, output_path):

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  ✏️  [1] 폰트 크기 설정 (FS_ 접두사)                         ║
    # ║  숫자만 바꾸면 바로 반영됩니다.                               ║
    # ║  bold=True 이면 굵은 글씨(malgunbd), False 이면 보통(malgun) ║
    # ╚══════════════════════════════════════════════════════════════╝

    # ── [헤더] ───────────────────────────────────────────────────────
    FS_HEADER_TITLE    = 34  # 좌측 "자산 실시간 분석" 제목 텍스트
    FS_HEADER_TIME     = 20  # 우측 "05월18일 오후 2:32" 날짜+시각
    FS_HEADER_INTERVAL = 16  # 우측 날짜 아래 "업데이트 주기: 1분" 작은 글씨
    FS_ICON_LABEL      = 18  # 각 카드 아이콘 컬럼 아래 라벨 ("자산 요약" 등)

    # ── [카드1] 자산 요약 ────────────────────────────────────────────
    #   좌측 위 섹션: 금일수익
    FS_C1_수익_라벨  = 30   # "금일수익" 회색 서브타이틀
    FS_C1_수익_배지  = 30   # 배지 안 텍스트 (예: ▼0.75%)
    FS_C1_수익_값    = 50   # 메인 금액값 (예: -₩5,064,708)

    #   좌측 아래 섹션: 총자산증감
    FS_C1_증감_라벨  = 30   # "총자산증감" 회색 서브타이틀
    FS_C1_증감_배지  = 30   # 배지 안 텍스트 (예: ▲57.60%)
    FS_C1_증감_값    = 34   # 메인 금액값 (예: ₩244,287,375)

    #   우측 위 섹션: 총자산
    FS_C1_총자산_라벨 = 30  # "총자산" 회색 서브타이틀
    FS_C1_총자산_값   = 50  # 메인 금액값 (예: ₩668,413,122)

    #   우측 아래 섹션: 은퇴시점
    FS_C1_은퇴_라벨  = 30   # "은퇴시점" 회색 서브타이틀
    FS_C1_은퇴_값    = 34   # 메인 금액값 (예: ₩65,512,937,544) — 축약 없이 전체 표시

    # ── [카드2] 투자 현황 ────────────────────────────────────────────
    #   좌측 컬럼: Exposure (레버리지 총 노출도)
    FS_C2_EXP_라벨   = 30   # "Exposure" 회색 서브타이틀
    FS_C2_EXP_값     = 50   # 메인 % 값 (예: 154.04%)
    FS_C2_EXP_금액   = 34   # 보조 금액값 (투자비중 금액, 회색)

    #   우측 컬럼: 현금비중
    FS_C2_현금_라벨  = 30   # "현금비중" 회색 서브타이틀
    FS_C2_현금_pct   = 50   # 메인 % 값 (예: 16.68%)
    FS_C2_현금_금액  = 34   # 보조 금액값 (예: ₩111,478,338, 회색)

    #   하단 스택바 범례 텍스트 (현금 / x1 / x2 / x3 각 비중 표시)
    FS_C2_스택_범례  = 22   # 예: "현금 17%", "x1 38%" 등

    # ── [카드3] 수익률 지표 ──────────────────────────────────────────
    FS_C3_IRR_라벨   = 30   # 각 지표 서브타이틀 ("월평균 IRR", "월평균 α", "최근 30일 α")
    FS_C3_IRR_값     = 50   # 각 지표 메인 % 값 (예: 4.05%)

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  ✏️  [2] 배지(태그) 스타일 설정                              ║
    # ║  배지: 라벨 옆에 붙는 색상 태그 (예: ▲57.60%, ▼0.75%)       ║
    # ║  pad_x: 좌우 내부 여백(px)                                   ║
    # ║  pad_y: 상하 내부 여백(px)                                   ║
    # ║  radius: 모서리 둥글기(px)                                   ║
    # ╚══════════════════════════════════════════════════════════════╝
    BADGE_PAD_X  = 9   # 배지 좌우 내부 여백
    BADGE_PAD_Y  = 0   # 배지 상하 내부 여백
    BADGE_RADIUS = 5   # 배지 모서리 둥글기

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  ✏️  [3] 카드 레이아웃 크기 설정                             ║
    # ║  카드 내부 텍스트가 잘리면 해당 카드 높이를 키워보세요        ║
    # ╚══════════════════════════════════════════════════════════════╝
    C1_H = 300   # 카드1(자산 요약) 높이 — 4개 섹션(금일수익/총자산증감/총자산/은퇴시점)
    C2_H = 260   # 카드2(투자 현황) 높이 — 상단 2컬럼 + 하단 스택바
    C3_H = 150   # 카드3(수익률 지표) 높이 — 3컬럼(IRR/월α/30일α)
    GAP  = 12    # 카드 사이 간격

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  ✏️  [4] 캔버스 및 헤더 크기                                 ║
    # ║  H는 카드 높이 합산으로 자동 계산됩니다.                      ║
    # ║  W만 변경하면 전체 가로 크기 조정 가능.                       ║
    # ╚══════════════════════════════════════════════════════════════╝
    HEADER_H     = 80   # 헤더 영역 높이 (제목 + 날짜/시각)
    CANVAS_PAD_T = 16   # 헤더 아래~카드1 사이 여백
    CANVAS_PAD_B = 20   # 카드3 아래 하단 여백
    W = 900
    H = HEADER_H + CANVAS_PAD_T + C1_H + GAP + C2_H + GAP + C3_H + CANVAS_PAD_B  # 자동 계산

    # ════════════════════════════════════════════════════════════════
    # 이하 색상·로직은 직접 수정하지 않아도 됩니다
    # ════════════════════════════════════════════════════════════════

    # ── 색상 상수 ────────────────────────────────────────────────────
    BG       = (8,  11, 18)
    CARD_BG  = (17, 21, 32)
    BORDER   = (30, 34, 53)
    GRAY     = (156, 163, 175)
    WHITE    = (241, 245, 249)
    RED      = (248, 113, 113)
    BLUE     = (96,  165, 250)
    GREEN    = (52,  211, 153)
    AMBER    = (251, 191,  36)
    RED_BG   = (31,  18,  18)
    BLUE_BG  = (15,  22,  40)
    GREEN_BG = (13,  32,  24)
    IC_BLUE  = (26,  42,  74)
    IC_GREEN = (15,  35,  24)
    IC_AMBER = (35,  26,   7)
    BAR_BG   = (26,  29,  40)

    # 스택바 색상 (위험도 낮음→높음 순)
    CLR_CASH = (96,  165, 250)   # 파랑  — 현금
    CLR_X1   = (251, 191,  36)   # 노랑  — x1 레버리지
    CLR_X2   = (251, 146,  60)   # 주황  — x2 레버리지
    CLR_X3   = (248, 113, 113)   # 빨강  — x3 레버리지

    # ── 데이터 로드 ──────────────────────────────────────────────────
    target_range = ws_inst.Range("A1").Value
    if not target_range:
        raise Exception("A1에 범위 정보 없음")

    data = ws_inst.Range(target_range).Value
    metrics = {}
    for row in data:
        k = str(row[0]).strip()
        v = str(row[1]).strip()
        if k and k not in ("-", "- -"):
            metrics[k] = v

    # ── 파싱 헬퍼 ────────────────────────────────────────────────────
    def parse_pct(raw: str):
        """'₩249,528,911 (58.83%)' → ('58.8%', 58.83, False)"""
        m = re.search(r'\((-?)(\d+\.?\d*)%\)', raw)
        if m:
            is_neg  = m.group(1) == "-"
            pct_val = float(m.group(2))
            return f"{pct_val:.2f}%", pct_val, is_neg
        return "", 0.0, False

    def amount_only(raw: str):
        """괄호 제거 + 앞의 마이너스 제거 → 금액 문자열만 반환"""
        s = re.sub(r'\s*\(.*?\)', '', raw).strip()
        return s.lstrip('-').strip()

    def to_eok(raw: str):
        """정수 문자열(쉼표 포함) → 억/조 단위 문자열
        예) '74679178913' → '746.8억'
        """
        num_str = re.sub(r'[^\d]', '', raw)
        if not num_str:
            return raw
        num = int(num_str)
        eok = num / 1_0000_0000
        if eok >= 10000:
            return f"{eok/10000:.1f}조"
        return f"{eok:.1f}억"

    def parse_x3x2x1(raw: str):
        """'24.6% | 21.2% | 37.6%' → (x3=24.6, x2=21.2, x1=37.6)"""
        nums = re.findall(r'[\d.]+', raw)
        if len(nums) >= 3:
            return float(nums[0]), float(nums[1]), float(nums[2])
        return 0.0, 0.0, 0.0

    # ── 각 항목 파싱 ─────────────────────────────────────────────────
    raw_증감 = metrics.get("총자산증감", "")
    raw_수익 = metrics.get("금일수익",  "")
    raw_현금 = metrics.get("현금비중",  "")
    raw_투자 = metrics.get("투자비중",  "")
    raw_lev  = metrics.get("x3/x2/x1", "")

    pct_증감_str, pct_증감_val, neg_증감 = parse_pct(raw_증감)
    pct_수익_str, pct_수익_val, neg_수익 = parse_pct(raw_수익)
    pct_현금_str, pct_현금_val, _        = parse_pct(raw_현금)
    pct_투자_str, pct_투자_val, _        = parse_pct(raw_투자)
    x3_val, x2_val, x1_val              = parse_x3x2x1(raw_lev)

    # 배지 텍스트: ▲/▼ + %
    badge_증감 = f"{'▼' if neg_증감 else '▲'}{pct_증감_str}"
    badge_수익 = f"{'▼' if neg_수익 else '▲'}{pct_수익_str}"

    # 색상: 양수=RED(상승), 음수=BLUE(하락)
    color_증감 = BLUE if neg_증감 else RED
    bg_증감    = BLUE_BG if neg_증감 else RED_BG
    color_수익 = BLUE if neg_수익 else RED
    bg_수익    = BLUE_BG if neg_수익 else RED_BG

    # 표시용 값
    v_총자산     = metrics.get("총자산", "")
    v_총자산증감  = amount_only(raw_증감)
    v_금일수익   = amount_only(raw_수익)
    v_현금_amt   = amount_only(raw_현금)
    v_투자_amt   = amount_only(raw_투자)
    v_exp        = metrics.get("Exposer", "")
    v_은퇴       = to_eok(metrics.get("은퇴시점", ""))

    # ── 이미지 / 드로우 초기화 ───────────────────────────────────────
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ── 폰트 로더 ────────────────────────────────────────────────────
    def font(bold=False, size=14):
        """bold=True → malgunbd.ttf, False → malgun.ttf"""
        try:
            return ImageFont.truetype("malgunbd.ttf" if bold else "malgun.ttf", size)
        except:
            return ImageFont.load_default()

    # 폰트 객체 생성 (위에서 정의한 FS_ 상수 사용)
    # 라벨(서브타이틀)은 보통체, 값/배지는 굵은체
    f_header_title    = font(True,  FS_HEADER_TITLE)     # 헤더 제목
    f_header_time     = font(False, FS_HEADER_TIME)      # 헤더 날짜+시각
    f_header_interval = font(False, FS_HEADER_INTERVAL)  # 헤더 업데이트 주기

    f_icon_label      = font(True,  FS_ICON_LABEL)       # 아이콘 아래 라벨

    # 카드1 폰트
    f_c1_수익_라벨    = font(False, FS_C1_수익_라벨)
    f_c1_수익_배지    = font(True,  FS_C1_수익_배지)
    f_c1_수익_값      = font(True,  FS_C1_수익_값)
    f_c1_증감_라벨    = font(False, FS_C1_증감_라벨)
    f_c1_증감_배지    = font(True,  FS_C1_증감_배지)
    f_c1_증감_값      = font(True,  FS_C1_증감_값)
    f_c1_총자산_라벨  = font(False, FS_C1_총자산_라벨)
    f_c1_총자산_값    = font(True,  FS_C1_총자산_값)
    f_c1_은퇴_라벨    = font(False, FS_C1_은퇴_라벨)
    f_c1_은퇴_값      = font(True,  FS_C1_은퇴_값)

    # 카드2 폰트
    f_c2_exp_라벨     = font(False, FS_C2_EXP_라벨)
    f_c2_exp_값       = font(True,  FS_C2_EXP_값)
    f_c2_exp_금액     = font(False, FS_C2_EXP_금액)
    f_c2_현금_라벨    = font(False, FS_C2_현금_라벨)
    f_c2_현금_pct     = font(True,  FS_C2_현금_pct)
    f_c2_현금_금액    = font(False, FS_C2_현금_금액)
    f_c2_스택_범례    = font(True,  FS_C2_스택_범례)

    # 카드3 폰트
    f_c3_irr_라벨     = font(False, FS_C3_IRR_라벨)
    f_c3_irr_값       = font(True,  FS_C3_IRR_값)

    # ── 드로잉 유틸 ──────────────────────────────────────────────────
    def tw(t, f):
        """텍스트 너비"""
        bb = draw.textbbox((0, 0), t, font=f)
        return bb[2] - bb[0]

    def th(t, f):
        """텍스트 높이"""
        bb = draw.textbbox((0, 0), t, font=f)
        return bb[3] - bb[1]

    def txt(x, y, t, f, c):
        draw.text((x, y), t, font=f, fill=c)

    def rrect(x1, y1, x2, y2, r, fill, outline=None, lw=1):
        draw.rounded_rectangle([x1, y1, x2, y2], radius=r,
                                fill=fill, outline=outline, width=lw)

    def hline(x1, x2, y, c):
        draw.line([(x1, y), (x2, y)], fill=c, width=1)

    def vline(x, y1, y2, c):
        draw.line([(x, y1), (x, y2)], fill=c, width=1)

    def badge_draw(x, y, text_str, fg, bg, f_badge):
        """배지(태그) 그리기. 배지 너비/높이 반환"""
        w = tw(text_str, f_badge) + BADGE_PAD_X * 2
        h = th(text_str, f_badge) + BADGE_PAD_Y * 2
        rrect(x, y, x + w, y + h, BADGE_RADIUS, bg)
        draw.text((x + BADGE_PAD_X, y + BADGE_PAD_Y), text_str, font=f_badge, fill=fg)
        return w, h

    # ── 아이콘 드로잉 ────────────────────────────────────────────────
    def draw_icon_wallet(cx, cy, r=18, color=BLUE):
        x1, y1, x2, y2 = cx-r, cy-int(r*0.65), cx+r, cy+int(r*0.65)
        rrect(x1, y1, x2, y2, 5, (0, 0, 0, 0), color, 2)
        hline(x1, x2, y1+int(r*0.5), color)
        rrect(x2-int(r*0.7), y1+int(r*0.65), x2-int(r*0.2), y1+int(r*0.95), 2, color)

    def draw_icon_donut(cx, cy, r=20, color=GREEN):
        inner = int(r * 0.48)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(*color, 80), width=2)
        draw.arc([cx-r, cy-r, cx+r, cy+r], start=-90, end=190, fill=color, width=6)
        draw.ellipse([cx-inner, cy-inner, cx+inner, cy+inner], fill=IC_GREEN)

    def draw_icon_chart(cx, cy, color=AMBER):
        pts = [(cx-20, cy+10), (cx-10, cy+2), (cx, cy+6), (cx+10, cy-6), (cx+20, cy-2)]
        for i in range(len(pts)-1):
            draw.line([pts[i], pts[i+1]], fill=color, width=2)
        draw.polygon([(cx+20, cy-2), (cx+16, cy-8), (cx+24, cy-8)], fill=color)

    def draw_icon_bars(x, y, color=BLUE):
        for bx, by, bw, bh, a in [(0,14,5,4,.6),(7,10,5,8,.7),(14,5,5,13,.85),(21,1,4,17,1.)]:
            c = tuple(int(v * a) for v in color)
            rrect(x+bx, y+by, x+bx+bw, y+by+bh, 1, c)

    def icon_circle(cx, cy, r, bg, draw_fn):
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=bg)
        draw_fn(cx, cy)

    # ── 레이아웃 상수 ────────────────────────────────────────────────
    # ✏️ 아래 값들도 필요 시 조정 가능합니다
    PAD         = 14   # 캔버스 좌우/상단 기본 여백 (px)
    ICON_COL    = 100   # 카드 좌측 아이콘 컬럼 너비 (px) — 아이콘 + 라벨 포함 영역
    ICON_R      = 26   # 아이콘 원 반지름 (px)
    ICON_GAP    = 14   # 아이콘 컬럼 오른쪽 끝 ~ 데이터 영역 시작까지 간격 (px)
    VLINE_PAD   = 14   # 카드 내부 세로 구분선의 상하 여백 (px)
    CARD_RADIUS = 14   # 카드 모서리 둥글기 (px)
    CARD_PAD_T  = 16   # 카드 내부 상단 여백 — 라벨 시작 y 오프셋 (px)
    LABEL_GAP   = 8    # 라벨(서브타이틀) ~ 값 사이 간격 (px)
    STACK_DOT_R = 8    # 스택바 범례 도트 반지름 (px)
    STACK_DOT_GAP = 28 # 스택바 범례 항목 사이 간격 (px)
    STACK_H     = 34   # 스택바 높이 (px)
    STACK_BAR_R = 6    # 스택바 모서리 둥글기 (px)

    # body: 아이콘 컬럼 제외한 실제 데이터 영역
    bx = PAD + ICON_COL + ICON_GAP   # body 시작 x
    bw = W - PAD - bx - 10           # body 너비

    # ══════════════════════════════════════════════════════════════
    # HEADER — 제목 아이콘 + 타이틀(좌) / 날짜+업데이트주기(우)
    # ══════════════════════════════════════════════════════════════

    # 막대그래프 아이콘: 타이틀과 세로 중앙 정렬
    # draw_icon_bars의 기준점(x,y)은 아이콘 좌상단 기준
    # 아이콘 실제 높이 약 22px → 헤더 중앙에 맞춤
    HEADER_ICON_H = 22   # draw_icon_bars 아이콘 실제 높이
    icon_y = (HEADER_H - HEADER_ICON_H)/2 + 6
    draw_icon_bars(PAD + 2, icon_y, BLUE)

    # 타이틀 텍스트: 헤더 세로 중앙 정렬
    title_h = th("자산 실시간 분석", f_header_title)
    title_y = (HEADER_H - title_h) // 2
    txt(PAD + 42, title_y, "자산 실시간 분석", f_header_title, WHITE)

    # 우측: 날짜+시각 (위) / 업데이트 주기 (아래) — 두 줄 합산 세로 중앙 정렬
    now    = datetime.now()
    hour   = now.hour
    ampm   = "오전" if hour < 12 else "오후"
    hour12 = hour % 12 or 12
    now_str      = now.strftime(f'%m월%d일 {ampm} {hour12}:%M')
    interval_str = f"업데이트 주기: {config.get('interval', 1)}분"
    time_h   = th(now_str,      f_header_time)
    intv_h   = th(interval_str, f_header_interval)
    total_rh = time_h + 4 + intv_h
    time_y   = (HEADER_H - total_rh) // 2
    intv_y   = time_y + time_h + 4
    txt(W - PAD - tw(now_str,      f_header_time),      time_y, now_str,      f_header_time,     GRAY)
    txt(W - PAD - tw(interval_str, f_header_interval),  intv_y, interval_str, f_header_interval, (75, 85, 99))

    # ══════════════════════════════════════════════════════════════
    # CARD 1 — 자산 요약
    # 레이아웃: [아이콘 컬럼] | [좌: 금일수익(위) / 총자산증감(아래)] | [우: 총자산(위) / 은퇴시점(아래)]
    # 좌/우 각각 sec_h(=C1_H//2) 높이로 균등 2분할, 가로도 균등 2분할
    # ══════════════════════════════════════════════════════════════
    C1_Y = HEADER_H + CANVAS_PAD_T   # 카드1 상단 y 좌표
    rrect(PAD, C1_Y, W-PAD, C1_Y+C1_H, CARD_RADIUS, CARD_BG, BORDER, 1)  # 카드 배경

    # ── 아이콘 컬럼: 지갑 아이콘 + "자산 요약" 라벨 ─────────────────
    # 아이콘은 카드 세로 중앙, 라벨은 카드 하단에서 28px 위
    icon_circle(PAD+ICON_COL//2, C1_Y+C1_H//2, ICON_R, IC_BLUE,
                lambda cx, cy: draw_icon_wallet(cx, cy, 18, BLUE))
    lbl_c1 = "자산 요약"
    txt(PAD+ICON_COL//2 - tw(lbl_c1, f_icon_label)//2,
        C1_Y+C1_H-28, lbl_c1, f_icon_label, BLUE)
    # 아이콘 컬럼 오른쪽 세로 구분선
    vline(PAD+ICON_COL+8, C1_Y+VLINE_PAD, C1_Y+C1_H-VLINE_PAD, BORDER)

    half  = bw // 2        # 좌/우 body 각 너비
    sec_h = C1_H // 2      # 상/하 섹션 각 높이 (4개 섹션을 2x2로 배치)

    # ── 좌측 위: 금일수익 (라벨 + 배지 + 금액값) ─────────────────────
    # 라벨과 배지는 같은 y에 가로로 나란히, 값은 라벨 아래 LABEL_GAP 간격
    txt(bx, C1_Y + CARD_PAD_T, "금일수익", f_c1_수익_라벨, GRAY)
    badge_draw(bx + tw("금일수익", f_c1_수익_라벨) + 10,
               C1_Y + CARD_PAD_T, badge_수익, color_수익, bg_수익, f_c1_수익_배지)
    txt(bx, C1_Y + CARD_PAD_T + th("금일수익", f_c1_수익_라벨) + LABEL_GAP,
        v_금일수익, f_c1_수익_값, color_수익)

    # ── 좌측 아래: 총자산증감 (가로 구분선 후 라벨 + 배지 + 금액값) ───
    sep_l = C1_Y + sec_h   # 좌측 상/하 구분선 y
    hline(bx, bx + half - 10, sep_l, BORDER)
    txt(bx, sep_l + CARD_PAD_T, "총자산증감", f_c1_증감_라벨, GRAY)
    badge_draw(bx + tw("총자산증감", f_c1_증감_라벨) + 10,
               sep_l + CARD_PAD_T, badge_증감, color_증감, bg_증감, f_c1_증감_배지)
    txt(bx, sep_l + CARD_PAD_T + th("총자산증감", f_c1_증감_라벨) + LABEL_GAP,
        v_총자산증감, f_c1_증감_값, color_증감)

    # ── 좌/우 세로 구분선 ─────────────────────────────────────────────
    vline(bx + half, C1_Y+VLINE_PAD, C1_Y+C1_H-VLINE_PAD, BORDER)
    rx = bx + half + 14   # 우측 컬럼 시작 x (구분선 오른쪽 14px 여백)

    # ── 우측 위: 총자산 (라벨 + 금액값) ──────────────────────────────
    txt(rx, C1_Y + CARD_PAD_T, "총자산", f_c1_총자산_라벨, GRAY)
    txt(rx, C1_Y + CARD_PAD_T + th("총자산", f_c1_총자산_라벨) + LABEL_GAP,
        v_총자산, f_c1_총자산_값, WHITE)

    # ── 우측 아래: 은퇴시점 (가로 구분선 후 라벨 + 금액값 전체 표시) ─
    sep_r    = C1_Y + sec_h   # 우측 상/하 구분선 y (좌측과 같은 y로 수평 정렬)
    hline(rx, W-PAD-10, sep_r, BORDER)
    v_은퇴_full = metrics.get("은퇴시점", "")   # 축약 없이 전체 금액 표시
    txt(rx, sep_r + CARD_PAD_T, "은퇴시점", f_c1_은퇴_라벨, GRAY)
    txt(rx, sep_r + CARD_PAD_T + th("은퇴시점", f_c1_은퇴_라벨) + LABEL_GAP,
        v_은퇴_full, f_c1_은퇴_값, WHITE)

    # ══════════════════════════════════════════════════════════════
    # CARD 2 — 투자 현황
    # 레이아웃: [아이콘 컬럼] | [Exposure(좌)] | [현금비중(우)]
    #           ──────────────────────────────────────────────────
    #           [스택바: 현금 / x1 / x2 / x3 비중]
    # 상단 데이터 영역: 카드 높이의 58%
    # 하단 스택바 영역: 나머지 42%
    # ══════════════════════════════════════════════════════════════
    C2_Y = C1_Y + C1_H + GAP   # 카드2 상단 y 좌표
    rrect(PAD, C2_Y, W-PAD, C2_Y+C2_H, CARD_RADIUS, CARD_BG, BORDER, 1)  # 카드 배경

    # ── 아이콘 컬럼: 도넛 아이콘 + "투자 현황" 라벨 ─────────────────
    # 아이콘은 상단 데이터 영역 중앙(카드 높이 38% 지점), 라벨은 카드 하단에서 28px 위
    C2_SPLIT = 0.58   # 상단 데이터 / 하단 스택바 비율 경계
    icon_circle(PAD+ICON_COL//2, C2_Y+int(C2_H*0.38), ICON_R, IC_GREEN,
                lambda cx, cy: draw_icon_donut(cx, cy, 20, GREEN))
    lbl_c2 = "투자 현황"
    txt(PAD+ICON_COL//2 - tw(lbl_c2, f_icon_label)//2,
        C2_Y+C2_H-28, lbl_c2, f_icon_label, GREEN)
    # 아이콘 컬럼 오른쪽 세로 구분선 (상단 데이터 영역까지만)
    vline(PAD+ICON_COL+8, C2_Y+VLINE_PAD, C2_Y+C2_H-VLINE_PAD, BORDER)

    col2w = bw // 2   # 2컬럼 균등 분할 너비

    def invest_col2(col_idx, label, main_str, main_color, sub_str, f_라벨, f_main, f_sub):
        """카드2 컬럼 그리기 (Exposure / 현금비중)
        - label: 회색 서브타이틀
        - main_str: 메인 강조값 (% 등)
        - sub_str: 보조 금액값 (회색, main 아래)
        - 모든 컬럼의 라벨/값 y 좌표를 동일하게 고정 → 세로 정렬 일치
        """
        x  = bx + col2w * col_idx
        ly = C2_Y + CARD_PAD_T                        # 라벨 y (모든 컬럼 동일)
        vy = ly + th(label, f_라벨) + LABEL_GAP        # 메인값 y
        sy = vy + th(main_str, f_main) + LABEL_GAP + 8    # 보조값 y
        txt(x, ly, label,    f_라벨, GRAY)
        txt(x, vy, main_str, f_main, main_color)
        if sub_str:
            txt(x, sy, sub_str, f_sub, GRAY)
        # 마지막 컬럼 제외 오른쪽 세로 구분선
        if col_idx < 1:
            vline(x + col2w, C2_Y+VLINE_PAD, C2_Y+int(C2_H*C2_SPLIT)-VLINE_PAD, BORDER)

    # Exposure: 메인=레버리지 노출도%, 보조=투자금액(회색)
    invest_col2(0, "Exposure", v_exp,        AMBER, v_투자_amt, f_c2_exp_라벨,  f_c2_exp_값,  f_c2_exp_금액)
    # 현금비중: 메인=현금비중%, 보조=현금금액(회색)
    invest_col2(1, "현금비중", pct_현금_str,  GREEN, v_현금_amt, f_c2_현금_라벨, f_c2_현금_pct, f_c2_현금_금액)

    # ── 스택바 영역: 상단/하단 구분선 후 범례 + 바 ───────────────────
    sep2    = C2_Y + int(C2_H * C2_SPLIT)   # 데이터와 스택바 구분선 y
    hline(bx, W-PAD-10, sep2, BORDER)

    stack_x = bx                          # 스택바 시작 x (body와 동일)
    stack_w = W - PAD - 10 - bx           # 스택바 너비 (body 전체 너비)
    label_y = sep2 + 14                   # 범례 도트+텍스트 시작 y
    bar_y   = label_y + FS_C2_스택_범례 + 14  # 스택바 본체 시작 y (범례 아래)

    # 비율 합산 — 소수점 오차 방지용, 0이면 100으로 대체
    total = pct_현금_val + x1_val + x2_val + x3_val
    if total == 0:
        total = 100

    # 스택 세그먼트 정의: (비율값, 색상, 범례라벨) — 위험도 낮음→높음 순
    segs = [
        (pct_현금_val, CLR_CASH, f"현금 {pct_현금_val:.0f}%"),
        (x1_val,       CLR_X1,   f"x1 {x1_val:.0f}%"),
        (x2_val,       CLR_X2,   f"x2 {x2_val:.0f}%"),
        (x3_val,       CLR_X3,   f"x3 {x3_val:.0f}%"),
    ]

    # 범례: 컬러 도트 + 텍스트를 가로로 나열
    cur_lx = stack_x
    for val, clr, lbl_s in segs:
        # 도트: 텍스트 세로 중앙에 맞춰 그리기
        draw.ellipse([cur_lx, label_y + STACK_DOT_R,
                      cur_lx + STACK_DOT_R*2, label_y + STACK_DOT_R*3], fill=clr)
        txt(cur_lx + STACK_DOT_R*2 + 4, label_y, lbl_s, f_c2_스택_범례, GRAY)
        cur_lx += tw(lbl_s, f_c2_스택_범례) + STACK_DOT_GAP

    # 스택바 배경 (회색 트랙)
    rrect(stack_x, bar_y, stack_x+stack_w, bar_y+STACK_H, STACK_BAR_R, BAR_BG)

    # 스택바 세그먼트 채우기 — 마지막 세그먼트는 남은 너비 전부 사용 (반올림 오차 방지)
    cur_x = stack_x
    for i, (val, clr, _) in enumerate(segs):
        seg_w = (stack_x + stack_w - cur_x) if i == len(segs)-1 \
                else int(stack_w * val / total)
        if seg_w > 0:
            # 첫/마지막 세그먼트만 좌/우 끝 둥글게, 중간은 직각
            r = STACK_BAR_R if (i == 0 or i == len(segs)-1) else 0
            rrect(cur_x, bar_y, cur_x+seg_w, bar_y+STACK_H, r, clr)
        cur_x += seg_w

    # ══════════════════════════════════════════════════════════════
    # CARD 3 — 수익률 지표
    # 레이아웃: [아이콘 컬럼] | [월평균IRR] | [월평균α] | [최근30일α]  3컬럼 균등
    # 각 컬럼의 라벨+값을 카드 세로 중앙 정렬
    # ══════════════════════════════════════════════════════════════
    C3_Y = C2_Y + C2_H + GAP   # 카드3 상단 y 좌표
    rrect(PAD, C3_Y, W-PAD, C3_Y+C3_H, CARD_RADIUS, CARD_BG, BORDER, 1)  # 카드 배경

    # ── 아이콘 컬럼: 차트 아이콘 + "수익률 지표" 라벨 ───────────────
    # 아이콘은 카드 세로 중앙, 라벨은 카드 하단에서 28px 위
    icon_circle(PAD+ICON_COL//2, C3_Y+C3_H//2, ICON_R, IC_AMBER,
                lambda cx, cy: draw_icon_chart(cx, cy, AMBER))
    lbl_c3 = "수익률 지표"
    txt(PAD+ICON_COL//2 - tw(lbl_c3, f_icon_label)//2,
        C3_Y+C3_H-28, lbl_c3, f_icon_label, AMBER)
    # 아이콘 컬럼 오른쪽 세로 구분선
    vline(PAD+ICON_COL+8, C3_Y+VLINE_PAD, C3_Y+C3_H-VLINE_PAD, BORDER)

    # ── 3컬럼 균등 분할: 라벨+값을 카드 세로 중앙에 정렬 ────────────
    col3w = bw // 3       # 컬럼 너비
    mid_y = C3_Y + C3_H // 2   # 카드 세로 중앙 y

    irr_items = [
        ("월평균 IRR",   metrics.get("월평균IRR",  ""), GREEN),
        ("월평균 α",    metrics.get("월평균α",    ""), GREEN),
        ("최근 30일 α",  metrics.get("최근30일α",  ""), GREEN),
    ]

    for i, (lbl_i, val, vc) in enumerate(irr_items):
        x  = bx + col3w * i
        lh = th(lbl_i, f_c3_irr_라벨)
        vh = th(val,   f_c3_irr_값)
        # (라벨높이 + LABEL_GAP + 값높이) 블록을 카드 세로 중앙에 맞춤
        ly = mid_y - (lh + LABEL_GAP + vh) // 2
        vy = ly + lh + LABEL_GAP
        txt(x, ly, lbl_i, f_c3_irr_라벨, GRAY)
        txt(x, vy, val,   f_c3_irr_값,   vc)
        # 마지막 컬럼 제외 오른쪽 세로 구분선
        if i < 2:
            vline(x + col3w, C3_Y+VLINE_PAD, C3_Y+C3_H-VLINE_PAD, BORDER)

    img.save(output_path)
    
def export_range_to_png(ws, rng_address, output_path):
    xl = ws.Application

    rng = ws.Range(rng_address)

    rng.CopyPicture(Format=2)

    chart_obj = ws.ChartObjects().Add(
        rng.Left,
        rng.Top,
        rng.Width,
        rng.Height
    )

    chart = chart_obj.Chart

    chart.Paste()

    chart.Export(str(output_path))

    chart_obj.Delete()
# ──
def send_telegram_photo(image_path, caption=""):
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id:
        return

    delete_telegram_message()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    try:
        with open(image_path, "rb") as f:

            files = {
                "photo": f
            }

            data = {
                "chat_id": chat_id,
                "caption": caption,
                "disable_notification": True
            }

            res = requests.post(
                url,
                files=files,
                data=data,
                timeout=30,
                verify=False
            ).json()

            if res.get("ok"):
                status["last_telegram_msg_id"] = res["result"]["message_id"]
                save_state()

                log.info(
                    f"새 이미지 메시지 전송 완료 "
                    f"(ID: {status['last_telegram_msg_id']})"
                )
            else:
                log.error(f"이미지 메시지 전송 실패: {res}")

    except Exception as e:
        log.error(f"텔레그램 이미지 전송 실패: {e}")
        
# ── 과거 데이터 조회 ──────────────────────────────────────────────────────────
def get_historical_price_yahoo(ticker, date_str):
    """야후 파이낸스에서 특정일 종가 조회 (IDX, NAS, AMS용)"""
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    p1 = int(dt.timestamp())
    p2 = int((dt + timedelta(days=4)).timestamp())  # 주말/공휴일 대비 여유있게
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={p1}&period2={p2}&interval=1d"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, verify=False).json()
    result = res["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    # 요청 날짜와 가장 가까운 종가 반환
    for ts, close in zip(timestamps, closes):
        if close and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") >= date_str:
            return close
    return closes[0] if closes else None

def get_historical_price_kr(ticker, date_str):
    """KIS API에서 특정일 종가 조회 (KR 종목용)"""
    token = get_access_token()
    url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": config["app_key"],
        "appsecret": config["app_secret"],
        "tr_id": "FHKST03010100",
        "custtype": "P"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": ticker,
        "fid_org_adj_prc": "1",
        "fid_period_div_code": "D",
        "fid_input_date_1": date_str.replace("-", ""),  # 예: "20260504"
        "fid_input_date_2": date_str.replace("-", ""),
    }
    res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    rows = res.json().get("output2", [])
    date_key = date_str.replace("-", "")
    for row in rows:
        if row.get("stck_bsop_date") == date_key:
            return float(row.get("stck_clpr", 0))
    # 정확한 날짜 없으면 가장 가까운 날짜 반환
    for row in rows:
        if row.get("stck_bsop_date") <= date_key:
            return float(row.get("stck_clpr", 0))
    return None

# ── 일간 누적 데이터 생성 ─────────────────────────────────────────────────────
def insert_daily_row():
    """
    config의 daily_sheet_name 시트에서:
    - A1 셀 값으로 샘플행 위치 읽기
    - 시트 내 첫 번째 테이블 자동 탐색
    - 샘플행 위에 새 행 삽입, 샘플행 복사 후 값으로 고정 + 색상 적용
    """
    import pythoncom
    pythoncom.CoInitialize()

    sheet_name = config.get("daily_sheet_name")
    past_color = int(config.get("daily_past_row_color",
                     255 + (217 * 256) + (179 * 256 * 256)))  # 기본: #FFD9B3

    if not sheet_name:
        log.error("일간 누적: config에 daily_sheet_name 이 없습니다.")
        return

    try:
        xl, wb, _ = open_excel_and_get_sheet()
        sheet = wb.Sheets(sheet_name)

        # A1 셀에서 샘플행 위치 읽기
        sample_row = int(sheet.Range("A1").Value)

        # 시트 내 첫 번째 테이블 자동 탐색
        table = sheet.ListObjects(1)

        # 1단계: 샘플행 위치에 표 행 삽입
        table.ListRows.Add(sample_row)
        log.info(f"일간 누적 1단계 완료: 테이블 '{table.Name}' {sample_row}번째 행 위에 삽입")

        new_row_range    = table.ListRows(sample_row).Range
        sample_row_range = table.ListRows(sample_row + 1).Range

        # 2단계: 샘플행을 새 빈 행에 복사 (수식 포함)
        sample_row_range.Copy(new_row_range)
        log.info("일간 누적 2단계 완료: 샘플행 복사")

        # 3단계: 샘플행을 값으로 고정
        sample_row_range.Copy()
        sample_row_range.PasteSpecial(Paste=-4163)  # xlPasteValues
        xl.CutCopyMode = False
        log.info("일간 누적 3단계 완료: 값으로 고정")

        # 4단계: 색상 적용
        sample_row_range.Interior.Color = past_color
        log.info("일간 누적 4단계 완료: 색상 적용")

    except Exception as e:
        log.error(f"일간 누적 데이터 생성 실패: {e}")


def enter_historical_mode(date_str, ws):
    """과거 데이터 조회 모드: 종목실시간 시트에 과거 종가 업데이트"""
    work_stopped_evt.clear()
    work_stop_evt.set()
    work_stopped_evt.wait(timeout=5)  # 최대 5초 대기
    work_stop_evt.clear()
    historical_mode_evt.set()
    
    update_event.set()   # 대기 중인 타이머 즉시 깨워서 historical_mode 체크로 진입
    status["last_status"] = f"과거조회: {date_str}"
    log.info(f"과거 데이터 조회 모드 시작: {date_str}")
    if tray_icon: tray_icon.update_menu()

    try:
        header_row, col_ticker, col_price, col_change, col_time, col_div, col_data_time = find_columns(ws)
        row = header_row + 1
        empty_count = 0
        updated = 0

        while True:
            if work_stop_evt.is_set():
                work_stopped_evt.set()
                break
            ticker_val = ws.Cells(row, col_ticker).Value
            if not ticker_val:
                empty_count += 1
                if empty_count >= 10:
                    break
                row += 1
                continue

            empty_count = 0
            ticker = str(ticker_val).strip()
            try: ticker = str(int(float(ticker)))
            except: pass

            div = str(ws.Cells(row, col_div).Value).strip() if col_div else ""

            try:
                price = None
                if div == "IDX":
                    price = get_historical_price_yahoo(ticker, date_str)
                    change_pct = 0
                elif div in ("NAS", "NYS", "AMS", "ARC"):
                    price = get_historical_price_yahoo(ticker, date_str)
                    change_pct = 0
                elif div == "KR":
                    price = get_historical_price_kr(ticker, date_str)
                    change_pct = 0
                else:
                    row += 1
                    continue

                if price:
                    if col_price: ws.Cells(row, col_price).Value = price
                    if col_change: ws.Cells(row, col_change).Value = 0
                    if col_time: ws.Cells(row, col_time).Value = date_str
                    if col_data_time: ws.Cells(row, col_data_time).Value = "과거데이터"
                    updated += 1
                    log.info(f"[과거] {ticker} ({div}): {price}")

                time.sleep(0.2)
            except Exception as e:
                log.warning(f"[과거] {ticker} ({div}) 오류: {e}", exc_info=True)

            row += 1

        log.info(f"과거 데이터 조회 완료: {updated}개 종목 ({date_str})")
        work_stopped_evt.set()  # ← 추가
        status["last_status"] = f"과거조회완료: {date_str} — 실시간재개 대기중"

    except Exception as e:
        log.error(f"과거 데이터 조회 실패: {e}")
        status["last_status"] = f"과거조회오류: {e}"

def exit_historical_mode():
    """실시간 모드로 복귀"""
    historical_mode_evt.clear()
    status["last_status"] = "정상"
    log.info("실시간 모드 복귀")
    if tray_icon: tray_icon.update_menu()
    update_event.set()  # 즉시 실시간 업데이트 재개

# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def update_loop():
    import pythoncom
    pythoncom.CoInitialize()
    xl = wb = ws = None

    while True:
        # 과거 데이터 조회 모드면 실시간 업데이트 건너뜀
        if historical_mode_evt.is_set():
            update_event.clear()  # 타이머 클리어
            time.sleep(1)
            continue

        try:
            if ws is None:
                xl, wb, ws = open_excel_and_get_sheet()

            header_row, col_ticker, col_price, col_change, col_time, col_div, col_data_time = find_columns(ws)
            now_str = datetime.now().strftime("%H:%M:%S")
            updated = 0
            row = header_row + 1
            empty_count = 0
            last_data_row = None

            while True:
                if work_stop_evt.is_set():
                    work_stopped_evt.set()
                    break

                ticker_val = ws.Cells(row, col_ticker).Value
                
                if not ticker_val:
                    empty_count += 1
                    if empty_count >= 10:
                        if last_data_row:
                            ws.Range(ws.Cells(last_data_row + 1, 1), ws.Cells(last_data_row + 1, ws.UsedRange.Columns.Count)).Interior.Color = 13882323
                        break
                    row += 1
                    continue
                
                empty_count = 0
                last_data_row = row
                ticker = str(ticker_val).strip()
                try: ticker = str(int(float(ticker)))
                except: pass
                    
                div = str(ws.Cells(row, col_div).Value).strip() if col_div else ""
                data_time = datetime.now()

                try:
                    if div == "KR": price, change_pct = get_kr_price(ticker)
                    elif div == "FX": price, change_pct = get_fx_rate(ticker)
                    elif div in ("NAS", "NYS", "AMS", "ARC"): price, change_pct = get_us_price(ticker, div)
                    elif div == "IDX": price, change_pct, data_time = get_index_price(ticker)
                    else: 
                        row += 1
                        continue

                    if col_price: ws.Cells(row, col_price).Value = price
                    if col_change: ws.Cells(row, col_change).Value = change_pct / 100
                    if col_time: ws.Cells(row, col_time).Value = now_str
                    if col_data_time:
                        delay = int((datetime.now() - data_time).total_seconds() / 60)
                        ws.Cells(row, col_data_time).Value = f"{delay}분 전"
                    updated += 1
                    time.sleep(0.1)
                except Exception as e:
                    log.warning(f"{ticker} 처리 중 오류: {e}")
                    
                row += 1

            log.info(f"{updated}개 종목 업데이트 완료 ({now_str})")
            work_stopped_evt.set()  # ← 추가 (정상완료도 알림)
            send_status_report(ws)
            status.update({"last_update": now_str, "last_heartbeat": datetime.now(), "last_status": "정상", "error": False})

        except Exception as e:
            log.error(f"업데이트 실패: {e}")
            status["last_status"] = f"오류: {e}"
            status["error"] = True
            xl = wb = ws = None

        update_event.clear()
        update_event.wait(timeout=config.get("interval", 1) * 60)
        # 과거 데이터 조회 모드면 재개 신호 올 때까지 대기
        while historical_mode_evt.is_set():
            time.sleep(1)

# ── 시스템 트레이 및 메뉴 (사용자 정의 기능 복구) ─────────────────────────────
def make_icon(state="normal"):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if state == "error": color = (220, 50, 50)
    elif state == "hang": color = (255, 200, 0)
    else: color = (50, 180, 50)
    draw.ellipse([4, 4, 60, 60], fill=color)
    draw.text((18, 18), "주식", fill="white")
    return img

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

def restart_app(icon, item):
    icon.stop()
    os.execl(sys.executable, sys.executable, *sys.argv)

def toggle_in_office(icon, item):
    global is_in_office
    is_in_office = not is_in_office
    log.info(f"사무실 모드: {'ON' if is_in_office else 'OFF'}")
    if tray_icon:
        tray_icon.update_menu()
        
def run_tray():
    def is_checked(min_val): return lambda item: config.get("interval") == min_val
    def set_interval(icon, item):
        config["interval"] = int(str(item).replace("분", ""))
        save_config()
        update_event.set()

    def open_historical_mode(icon, item):
        """날짜 입력창 띄우고 과거 데이터 조회 모드 진입"""
        def show_dialog():
            import tkinter as tk
            from tkinter import messagebox

            result = {"date": None}

            def on_confirm():
                result["date"] = entry.get().strip()
                dlg.destroy()

            def on_cancel():
                dlg.destroy()

            dlg = tk.Tk()
            dlg.title("과거 데이터 조회")
            dlg.geometry("320x120")
            dlg.resizable(False, False)
            dlg.attributes("-topmost", True)
            dlg.lift()
            dlg.focus_force()

            tk.Label(dlg, text="조회할 날짜를 입력하세요 (예: 2025-01-04)", pady=10).pack()
            entry = tk.Entry(dlg, width=20, font=("Arial", 13))
            entry.pack(pady=4)
            entry.insert(0, datetime.now().strftime("%Y-%m-%d"))
            entry.select_range(0, tk.END)
            entry.focus_set()

            btn_frame = tk.Frame(dlg)
            btn_frame.pack(pady=8)
            tk.Button(btn_frame, text="확인", width=8, command=on_confirm).pack(side=tk.LEFT, padx=4)
            tk.Button(btn_frame, text="취소", width=8, command=on_cancel).pack(side=tk.LEFT, padx=4)
            dlg.bind("<Return>", lambda e: on_confirm())
            dlg.bind("<Escape>", lambda e: on_cancel())

            dlg.mainloop()

            date_str = result["date"]
            if not date_str:
                return

            # 날짜 형식 검증
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                root2 = tk.Tk()
                root2.withdraw()
                messagebox.showerror("오류", "날짜 형식이 올바르지 않습니다.\n예: 2025-01-04")
                root2.destroy()
                return

            # 별도 스레드에서 과거 조회 실행
            def run():
                try:
                    import pythoncom
                    pythoncom.CoInitialize()
                    _, _, ws = open_excel_and_get_sheet()
                    enter_historical_mode(date_str, ws)
                except Exception as e:
                    log.error(f"과거 조회 스레드 오류: {e}")

            threading.Thread(target=run, daemon=True).start()

        threading.Thread(target=show_dialog, daemon=True).start()

    icon = Icon("stock_updater", make_icon(), "주식 업데이터", menu=Menu(
        MenuItem(lambda item: f"마지막: {status['last_update']}", None, enabled=False),
        MenuItem(lambda item: f"상태: {status['last_status']}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("🚀 즉시 업데이트", lambda i, n: update_event.set(),
                 enabled=lambda item: not historical_mode_evt.is_set()),
        MenuItem('⏱️ 주기 설정', Menu(
            MenuItem('1분', set_interval, checked=is_checked(1), radio=True),
            MenuItem('3분', set_interval, checked=is_checked(3), radio=True),
            MenuItem('5분', set_interval, checked=is_checked(5), radio=True),
            MenuItem('10분', set_interval, checked=is_checked(10), radio=True)
        ), enabled=lambda item: not historical_mode_evt.is_set()),
        Menu.SEPARATOR,
        MenuItem("📡 사내 모드",toggle_in_office, checked=lambda item:is_in_office),
        MenuItem("📊 일간 누적 데이터 생성", lambda i, n: threading.Thread(target=insert_daily_row, daemon=True).start()),
        Menu.SEPARATOR,
        MenuItem("📅 과거 데이터 조회", open_historical_mode),
        MenuItem("▶ 실시간 재개", lambda i, n: exit_historical_mode(),
                 enabled=lambda item: historical_mode_evt.is_set()),
        Menu.SEPARATOR,
        MenuItem("로그 보기", lambda i, n: os.startfile(str(LOG_FILE))),
        MenuItem("프로그램 재시작", restart_app),
        MenuItem("종료", quit_app),
    ))

    def watchdog():
        while True:
            time.sleep(5)
            diff = (datetime.now() - status["last_heartbeat"]).total_seconds()
            if diff >= 600: restart_app(icon, None)
            elif diff >= 300: icon.icon = make_icon("hang")
            else: icon.icon = make_icon("error" if status["error"] else "normal")
    
    threading.Thread(target=watchdog, daemon=True).start()
    global tray_icon
    tray_icon = icon
    icon.run()

if __name__ == "__main__":
    config = load_config()
    load_state()
    threading.Thread(target=update_loop, daemon=True).start()
    run_tray()
