"""
common/kis_lookup.py
KIS(한국투자증권) 국내/해외 종목 기본정보 조회.

책임 분리 (kis_auth.py와의 관계):
  - kis_auth.py: access_token 발급/캐시(인증)만 담당
  - kis_lookup.py: 종목 조회 API 호출(이 모듈)만 담당
  appkey/appsecret은 kis_auth.py의 컨벤션과 동일하게 이 모듈이 스스로
  scheduler/config.json에서 로드한다 ("공용 모듈은 필요한 리소스를 스스로 확보한다").

실측 확인된 사실 (scheduler/test.py로 직접 호출해 확인, 2026-07-17):
  - 국내 조회(tr_id CTPF1002R): PRDT_TYPE_CD="300" 하나로 코스피/코스닥/코넥스 전체 커버.
    레버리지 배수는 output.etf_chas_erng_rt_dbnb. 일반주(SK하이닉스)="0",
    비레버리지 ETF(KODEX 반도체)="1", 2배 레버리지 ETF(TIGER 필라델피아반도체레버리지)="2".
  - 해외 조회(tr_id CTPF1702R): PRDT_TYPE_CD 512(나스닥)/513(뉴욕)/529(아멕스) 순회 필요
    (하나의 코드로 전체를 커버하지 못함 — 국내와 다름). 레버리지 배수는
    output.etp_chas_erng_rt_dbnb. 일반주(AAPL)="0.000000", 3배 ETN(BULZ)="3.000000".
  - PDNO는 호출자가 넘긴 문자열을 그대로 사용한다. 이 모듈이 임의로 접두사(Q 등)를
    붙이거나 떼지 않는다 — 국내 종목코드 중 앞자리 생략 시 서로 다른 상품(ELW vs ETN)이
    같은 코드로 겹치는 사례가 실측으로 확인되었기 때문에, 이 판단은 호출자(사용자 입력)의
    책임으로 남겨둔다.

레버리지 값 해석 정책:
  - 0 또는 1 → 레버리지 없음(1배)으로 정규화.
  - 절대값이 정수이고 1~3 범위 → 그 정수를 그대로 반환.
  - 그 외(정수가 아니거나 3배 초과, 예: 1.5배) → None 반환.
    호출자(accounts.py)는 None이면 UI의 레버리지 선택값을 자동으로 세팅하지 않고
    사용자가 직접 고르도록 둔다 (accounts_modals.py의 select가 x1/x2/x3 고정 옵션이라
    범위 밖 값을 넣을 수 없기 때문 — 임의로 반올림해 잘못된 값을 자동 세팅하지 않는다).
"""

import json
from pathlib import Path
from typing import Optional

import requests
import urllib3

from common.kis_auth import get_kis_access_token, KISAuthError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

URL_DOMESTIC = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/search-stock-info"
URL_OVERSEAS = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/search-info"

# PRDT_TYPE_CD -> 내부 마켓 코드 (config.json market_map과 일치)
_OVERSEAS_MARKETS = [
    ("512", "NAS"),
    ("513", "NYS"),
    ("529", "AMS"),
]


def _load_kis_credentials() -> tuple:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return config["kis_app_key"], config["kis_app_secret"]


def _resolve_leverage(raw) -> Optional[int]:
    """
    KIS 레버리지 배수 필드(etf_chas_erng_rt_dbnb / etp_chas_erng_rt_dbnb) 해석.
    0/1 -> 1, 정수 1~3 -> 그 값, 그 외(비정수·3배 초과 등) -> None (자동세팅 보류).
    """
    if raw is None or raw == "":
        return 1
    try:
        val = abs(float(raw))
    except (TypeError, ValueError):
        return None
    if val == 0:
        return 1
    if val == int(val) and 1 <= int(val) <= 3:
        return int(val)
    return None


def lookup_domestic(symbol: str) -> Optional[dict]:
    """
    국내 주식/ETF/ETN 조회 (tr_id CTPF1002R).
    성공: {"name": str, "market": "KR", "leverage": int|None}
    실패(rt_cd != "0", 네트워크/파싱 오류 포함): None
    """
    try:
        token = get_kis_access_token()
    except KISAuthError as e:
        print(f"[kis_lookup] 토큰 발급 실패 (국내 조회 중단): {e}")
        return None

    appkey, appsecret = _load_kis_credentials()
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "CTPF1002R",
        "custtype": "P",
    }
    params = {
        "PRDT_TYPE_CD": "300",
        "PDNO": symbol,
    }

    try:
        r = requests.get(URL_DOMESTIC, headers=headers, params=params, timeout=10, verify=False)
        data = r.json()
    except Exception as e:
        print(f"[kis_lookup] 국내 조회 요청/파싱 실패 ({symbol}): {e}")
        return None

    if data.get("rt_cd") != "0":
        return None

    out = data.get("output", {})
    name = out.get("prdt_name") or out.get("prdt_abrv_name") or ""
    leverage = _resolve_leverage(out.get("etf_chas_erng_rt_dbnb"))

    return {"name": name, "market": "KR", "leverage": leverage}


def lookup_overseas(symbol: str) -> Optional[dict]:
    """
    해외 주식/ETF/ETN 조회 (tr_id CTPF1702R). 512(나스닥)→513(뉴욕)→529(아멕스) 순회.
    성공: {"name": str, "market": "NAS"|"NYS"|"AMS", "leverage": int|None}
    실패(전 시장 rt_cd != "0", 네트워크/파싱 오류 포함): None
    """
    try:
        token = get_kis_access_token()
    except KISAuthError as e:
        print(f"[kis_lookup] 토큰 발급 실패 (해외 조회 중단): {e}")
        return None

    appkey, appsecret = _load_kis_credentials()
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": "CTPF1702R",
        "custtype": "P",
    }

    for prdt_type, market_code in _OVERSEAS_MARKETS:
        params = {
            "PRDT_TYPE_CD": prdt_type,
            "PDNO": symbol,
        }
        try:
            r = requests.get(URL_OVERSEAS, headers=headers, params=params, timeout=10, verify=False)
            data = r.json()
        except Exception as e:
            print(f"[kis_lookup] 해외 조회 요청/파싱 실패 ({symbol}, {market_code}): {e}")
            continue

        if data.get("rt_cd") == "0":
            out = data.get("output", {})
            name = out.get("prdt_name") or out.get("prdt_eng_name") or ""
            leverage = _resolve_leverage(out.get("etp_chas_erng_rt_dbnb"))
            return {"name": name, "market": market_code, "leverage": leverage}

    return None
