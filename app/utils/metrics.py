import datetime
from decimal import Decimal
import numpy as np
import scipy.optimize as optimize

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

        # 💡 [교정] 미국 주식 등 해외 종목이거나 market이 US/FX인 경우 환율을 명확히 곱해줍니다.
        if ticker == "KRW":
            eval_krw = qty
        elif ticker == "USD":
            eval_krw = qty * usd_krw
        elif market.upper() in ("NAS", "AMS", "ARC"):
            eval_krw = qty * price * usd_krw
        else:
            eval_krw = qty * price

        # 익스포저 제외 자산 분류
        if ticker in ("KRW", "USD") or market.upper() in ("FX", "INDEX"):
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
            "x1_ratio": 0.0, "x2_ratio": 0.0, "x3_ratio": 0.0
        }

    return {
        "total_asset": total_asset,
        "exposure": weighted_exposure / total_asset,
        "cash_ratio": (total_asset - stock_eval) / total_asset,
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