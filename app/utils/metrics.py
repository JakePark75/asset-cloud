import datetime
from decimal import Decimal
import numpy as np
import scipy.optimize as optimize
from app.db import get_market_currency

def to_f(val) -> float:
    if val is None: return 0.0
    return float(val)

def calculate_exposure_and_ratios(db_rows: list[tuple], usd_krw: float) -> dict:
    """
    [순수 계산기] 포지션-티커 데이터를 받아 원화 환산 및 비중 지표를 계산합니다.
    db_rows 규칙: (ticker, quantity, current_price, leverage, market)
    """
    cash_eval = 0.0
    stock_eval = 0.0
    weighted_exposure = 0.0
    x1_eval, x2_eval, x3_eval = 0.0, 0.0, 0.0

    for ticker, qty, price, leverage, market in db_rows:
        qty = to_f(qty)
        price = to_f(price)
        leverage = int(leverage) if leverage else 1
        market = market if market else ""

        if ticker == "KRW":
            eval_krw = qty
        elif get_market_currency(market) == "USD":
            eval_krw = qty * price * usd_krw
        else:
            eval_krw = qty * price

        # 익스포저 제외 자산 분류
        if ticker in ("KRW", "USD"):
            cash_eval += eval_krw
        else:
            stock_eval += eval_krw
            weighted_exposure += (eval_krw * leverage)
            
            if leverage == 1:   x1_eval += eval_krw
            elif leverage == 2: x2_eval += eval_krw
            elif leverage == 3: x3_eval += eval_krw

    total_asset = cash_eval + stock_eval
    
    if total_asset == 0:
        return {
            "total_asset": 0.0, "exposure": 0.0, "cash_ratio": 0.0,
            "cash_eval": 0.0, "x1_ratio": 0.0, "x2_ratio": 0.0, "x3_ratio": 0.0
        }

    return {
        "total_asset": total_asset,
        "exposure": weighted_exposure / total_asset,
        "cash_ratio": (total_asset - stock_eval) / total_asset,
        "cash_eval": cash_eval,
        "x1_ratio": x1_eval / total_asset,
        "x2_ratio": x2_eval / total_asset,
        "x3_ratio": x3_eval / total_asset
    }

def calculate_xirr(cash_flows: list[tuple]) -> float:
    if not cash_flows or len(cash_flows) < 2: return 0.0
    dates = [cf[0] for cf in cash_flows]
    amounts = [to_f(cf[1]) for cf in cash_flows]
    t0 = dates[0]
    t = np.array([(d - t0).days / 365.0 for d in dates])
    vals = np.array(amounts)
    f = lambda r: np.sum(vals / ((1 + r) ** t))
    try: return float(optimize.newton(f, 0.1, maxiter=100))
    except: return 0.0

def calculate_alpha(start_row: tuple, end_row: tuple) -> float:
    if not start_row or not end_row: return 0.0
    my_start, bch_start = to_f(start_row[0]), to_f(start_row[1])
    my_end, bch_end = to_f(end_row[0]), to_f(end_row[1])
    if my_start == 0 or bch_start == 0: return 0.0
    return ((my_end / my_start) - 1.0) - ((bch_end / bch_start) - 1.0)

def calculate_monthly_irr(cash_flows: list[tuple]) -> float:
    """
    XIRR(연환산) → 월환산 IRR 반환
    cash_flows: [(date, amount), ...] — 입출금은 음수, 현재 자산은 양수 마지막 항목
    반환값: 월 수익률 (예: 0.02 = 2%)
    """
    annual_irr = calculate_xirr(cash_flows)
    if annual_irr <= -1.0: return 0.0
    return (1 + annual_irr) ** (1 / 12) - 1

def calculate_daily_profit(today_asset: float, yesterday_asset: float) -> float:
    """
    금일 손익 = 오늘 실시간 총평가액 - daily_summary 마지막 행 total_asset
    입출금 보정 없음 (입출금 있는 날은 오차 감수)
    """
    if yesterday_asset == 0: return 0.0
    return today_asset - yesterday_asset

def calculate_retirement_asset(total_asset: float, monthly_irr: float, retirement_date: datetime.date) -> float:
    """
    은퇴 시점 예상 자산액
    현재 총자산에 월평균 IRR 복리 적용
    monthly_irr: 월 수익률 (예: 0.02 = 2%)
    retirement_date: 은퇴 목표일
    """
    if monthly_irr <= -1.0 or total_asset <= 0: return 0.0
    today = datetime.date.today()
    if retirement_date <= today: return total_asset
    months = (retirement_date.year - today.year) * 12 + (retirement_date.month - today.month)
    if months <= 0: return total_asset
    return total_asset * ((1 + monthly_irr) ** months)

def calculate_beta(rows: list[tuple]) -> float:
    """
    포트폴리오 베타 (vs NDX100)
    rows: [(total_asset, ndx100), ...] 날짜 오름차순
    일별 수익률 기반 공분산 / NDX100 분산
    반환값: 베타 (예: 1.5)
    """
    if not rows or len(rows) < 3: return 0.0
    assets = np.array([to_f(r[0]) for r in rows])
    ndx    = np.array([to_f(r[1]) for r in rows])
    if np.any(assets[:-1] == 0) or np.any(ndx[:-1] == 0): return 0.0
    my_ret  = np.diff(assets) / assets[:-1]
    ndx_ret = np.diff(ndx)    / ndx[:-1]
    var_ndx = np.var(ndx_ret, ddof=1)
    if var_ndx == 0: return 0.0
    cov = np.cov(my_ret, ndx_ret, ddof=1)[0][1]
    return float(cov / var_ndx)