"""
common/kis_auth.py
KIS(한국투자증권) REST 접근토큰(access_token) / 웹소켓 접속키(approval_key) 통합 관리 모듈.

배경:
  기존에는 price_updater_common.py / price_updater_ws.py / daily_snapshot.py / snap.py
  4곳에 토큰 발급 로직이 중복 구현되어 있었고, 그중 daily_snapshot.py / price_updater_ws.py는
  만료 관리가 없어 24시간 이상 프로세스가 살아있으면 만료된 토큰/접속키를 계속 사용할
  가능성이 있었다. 이 모듈로 통합한다.

설계 (합의된 방향):
  - 저장소: Redis (common/redis_store.py의 get_redis() 재사용 — 별도 연결 방식 만들지 않음)
  - 프로세스 내부: 초 단위로 반복 호출되는 고빈도 경로(가격 조회 등)에서 매번 Redis를
    때리지 않도록, 프로세스 로컬 메모리 캐시를 앞단에 둔다.
  - 프로세스 간 동시성: Redis 락(SET NX EX)으로 KIS 실제 발급 요청이 항상 1번만 나가도록 보장.
    - 락 EX(10초)는 "정상 동작 시간"을 재는 값이 아니라, 발급 도중 프로세스가 죽는 등
      비정상 종료 상황에서 락이 영원히 안 풀리는 데드락을 막기 위한 안전장치다.
      정상 흐름에서는 발급 완료 즉시 명시적으로 락을 해제하므로 이 값이 실제로
      소진되는 일은 없다.
    - 락 해제는 Lua 스크립트로 "내가 잡은 락이 맞는지 확인 후 삭제"를 원자적으로 수행한다
      (GET 후 DEL을 따로 하면 그 사이 다른 프로세스의 락을 잘못 지울 수 있음).
  - 캐시 유효 기준: 만료까지 REFRESH_MARGIN_SEC(5분) 이상 남아있으면 캐시 값을 그대로 사용.
  - appkey/appsecret 출처: 이 모듈이 scheduler/config.json을 직접 읽는다.
    (redis_store.py가 Redis 연결을, db.py가 DB 접속정보를 각각 스스로 확보하는 것과
    동일한 컨벤션 — "공용 모듈은 필요한 리소스를 스스로 확보한다")
  - Redis 키 네이밍: REST/WS를 분리한다 (두 토큰은 서로 다른 API 엔드포인트에서
    독립적으로 발급되므로 락도 따로 둬야 서로 안 걸린다).
      kis:rest:access_token / kis:rest:token_lock
      kis:ws:approval_key   / kis:ws:token_lock

노출 함수:
  get_kis_access_token()   — REST 접근토큰
  get_kis_approval_key()   — 웹소켓 접속키

주의 (사실 확인됨, 이 모듈 설계의 전제):
  - KIS 공식문서 기준 access_token/approval_key 유효기간은 둘 다 24시간이다.
  - KIS 접근토큰 발급 응답에는 expires_in 필드가 있어 이를 사용한다.
  - approval_key 발급 응답에는 만료시각 관련 필드가 없어(기존 4개 파일 코드 확인 결과
    approval_key 문자열만 반환됨), 공식 문서상 유효기간(24시간)을 발급 시각 기준으로
    직접 계산해 사용한다. KIS가 실제 만료를 이보다 일찍 시키는 경우까지는 이 모듈이
    보장하지 못한다 — 이 부분은 추론이 아니라 "API 응답 자체에 만료시각이 없다"는
    관찰된 사실에 기반한 설계 선택이다.
"""

import json
import threading
import time
import uuid
from pathlib import Path

import requests
import urllib3

from common.redis_store import get_redis

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

# KIS 공식 문서 기준: approval_key 유효기간 24시간 (응답에 만료시각 필드가 없어 직접 계산)
APPROVAL_KEY_TTL_SEC = 24 * 60 * 60

# 만료까지 이 값(초) 미만으로 남으면 재발급 대상으로 간주
REFRESH_MARGIN_SEC = 5 * 60

# Redis 락 EX(초) — 비정상 종료(크래시 등) 대비 안전장치. 정상 흐름에서는 소진되지 않음.
LOCK_EX_SEC = 10

# 락을 다른 프로세스가 쥐고 있을 때, 대기하며 재조회하는 주기/최대 시간(초)
POLL_INTERVAL_SEC = 0.3
MAX_WAIT_SEC = 15

# Redis 키
_REST_TOKEN_KEY = "kis:rest:access_token"
_REST_LOCK_KEY  = "kis:rest:token_lock"
_WS_TOKEN_KEY   = "kis:ws:approval_key"
_WS_LOCK_KEY    = "kis:ws:token_lock"

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class KISAuthError(Exception):
    """KIS 토큰(access_token) / 접속키(approval_key) 발급 실패."""
    pass


# ---------------------------------------------------------------------------
# 프로세스 로컬 캐시 (고빈도 호출에서 Redis 왕복 자체를 없애기 위함)
# ---------------------------------------------------------------------------
_local_cache: dict = {}
_local_cache_lock = threading.Lock()

# cache_key별 발급 작업을 프로세스 내에서 직렬화 (동시 다발 스레드가 각자 Redis를
# 두드리는 것을 줄이기 위함 — price_updater_rest.py의 update_worker처럼 티커별로
# 스레드를 띄우는 경로에서 특히 의미가 있다)
_key_locks: dict = {}
_key_locks_meta_lock = threading.Lock()


def _get_key_lock(cache_key: str) -> threading.Lock:
    with _key_locks_meta_lock:
        if cache_key not in _key_locks:
            _key_locks[cache_key] = threading.Lock()
        return _key_locks[cache_key]


def _get_local(cache_key: str):
    with _local_cache_lock:
        entry = _local_cache.get(cache_key)
    if entry and entry["expires_at"] - time.time() >= REFRESH_MARGIN_SEC:
        return entry["value"]
    return None


def _set_local(cache_key: str, value: str, expires_at: float) -> None:
    with _local_cache_lock:
        _local_cache[cache_key] = {"value": value, "expires_at": expires_at}


# ---------------------------------------------------------------------------
# appkey/appsecret
# ---------------------------------------------------------------------------
def _load_kis_credentials() -> tuple:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return config["kis_app_key"], config["kis_app_secret"]


# ---------------------------------------------------------------------------
# 실제 KIS API 호출 (재발급)
# ---------------------------------------------------------------------------
def _fetch_access_token() -> tuple:
    """REST 접근토큰 발급. 반환: (token, expires_at unix ts)"""
    appkey, appsecret = _load_kis_credentials()
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "appsecret": appsecret,
    }
    try:
        res = requests.post(url, json=body, timeout=10, verify=False)
    except requests.exceptions.RequestException as e:
        raise KISAuthError(f"access_token 발급 요청 실패 (네트워크): {e}")

    try:
        data = res.json()
    except Exception as e:
        raise KISAuthError(f"access_token 응답 파싱 실패: {e} / body={res.text[:200]}")

    token = data.get("access_token")
    if not token:
        raise KISAuthError(f"access_token 발급 실패: {res.text[:200]}")

    expires_in = int(data.get("expires_in", 86400))
    expires_at = time.time() + expires_in
    print("[kis_auth] REST access_token 발급 완료")
    return token, expires_at


def _fetch_approval_key() -> tuple:
    """웹소켓 접속키 발급. 반환: (approval_key, expires_at unix ts)"""
    appkey, appsecret = _load_kis_credentials()
    url = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "secretkey": appsecret,
    }
    try:
        res = requests.post(url, json=body, timeout=10, verify=False)
    except requests.exceptions.RequestException as e:
        raise KISAuthError(f"approval_key 발급 요청 실패 (네트워크): {e}")

    try:
        key = res.json().get("approval_key", "")
    except Exception as e:
        raise KISAuthError(f"approval_key 응답 파싱 실패: {e} / body={res.text[:200]}")

    if not key:
        raise KISAuthError(f"approval_key 발급 실패: {res.text[:200]}")

    expires_at = time.time() + APPROVAL_KEY_TTL_SEC
    print("[kis_auth] WS approval_key 발급 완료")
    return key, expires_at


# ---------------------------------------------------------------------------
# Redis 락 해제 (원자적 — 소유자 확인 후 삭제)
# ---------------------------------------------------------------------------
def _release_lock(r, lock_key: str, owner_token: str) -> None:
    try:
        r.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, owner_token)
    except Exception as e:
        print(f"[kis_auth] 락 해제 실패 ({lock_key}): {e} — EX 만료로 자동 해제될 예정이라 무시")


# ---------------------------------------------------------------------------
# 캐시 + 락 공통 로직
# ---------------------------------------------------------------------------
def _get_cached_or_refresh(cache_key: str, lock_key: str, fetch_fn) -> str:
    # 1) 프로세스 로컬 캐시 — 대부분의 호출은 여기서 끝난다 (Redis 왕복 없음)
    value = _get_local(cache_key)
    if value:
        return value

    key_lock = _get_key_lock(cache_key)
    with key_lock:
        # 락 획득 대기 중 다른 스레드가 이미 채웠을 수 있으므로 재확인
        value = _get_local(cache_key)
        if value:
            return value

        r = get_redis()
        if not r:
            # Redis 장애 시: 캐시/프로세스간 조율 없이 직접 발급 (핵심 기능이므로
            # write_price류처럼 조용히 넘어갈 수 없음 — 발급 자체는 계속 시도한다)
            print(f"[kis_auth] Redis 연결 불가 — {cache_key} 캐시 없이 직접 발급")
            value, expires_at = fetch_fn()
            _set_local(cache_key, value, expires_at)
            return value

        try:
            raw = r.get(cache_key)
            if raw:
                cached = json.loads(raw)
                if cached["expires_at"] - time.time() >= REFRESH_MARGIN_SEC:
                    _set_local(cache_key, cached["value"], cached["expires_at"])
                    return cached["value"]

            owner_token = str(uuid.uuid4())
            acquired = r.set(lock_key, owner_token, nx=True, ex=LOCK_EX_SEC)

            if acquired:
                try:
                    # 락 획득 후 재확인 (그 사이 다른 프로세스가 갱신했을 수 있음)
                    raw = r.get(cache_key)
                    if raw:
                        cached = json.loads(raw)
                        if cached["expires_at"] - time.time() >= REFRESH_MARGIN_SEC:
                            _set_local(cache_key, cached["value"], cached["expires_at"])
                            return cached["value"]

                    value, expires_at = fetch_fn()
                    r.set(cache_key, json.dumps({"value": value, "expires_at": expires_at}))
                    _set_local(cache_key, value, expires_at)
                    return value
                finally:
                    _release_lock(r, lock_key, owner_token)
            else:
                # 다른 프로세스가 발급 중 — 완료될 때까지 대기 후 재조회
                waited = 0.0
                while waited < MAX_WAIT_SEC:
                    time.sleep(POLL_INTERVAL_SEC)
                    waited += POLL_INTERVAL_SEC
                    raw = r.get(cache_key)
                    if raw:
                        cached = json.loads(raw)
                        if cached["expires_at"] - time.time() >= REFRESH_MARGIN_SEC:
                            _set_local(cache_key, cached["value"], cached["expires_at"])
                            return cached["value"]

                # 대기 시간 초과 — 안전망: 직접 발급 (락 보유 프로세스에 문제가 생겼을 가능성)
                print(f"[kis_auth] {lock_key} 대기 {MAX_WAIT_SEC}초 초과 — 직접 발급으로 폴백")
                value, expires_at = fetch_fn()
                r.set(cache_key, json.dumps({"value": value, "expires_at": expires_at}))
                _set_local(cache_key, value, expires_at)
                return value
        except KISAuthError:
            raise
        except Exception as e:
            print(f"[kis_auth] Redis 캐시/락 처리 중 오류 ({cache_key}): {e} — 직접 발급으로 폴백")
            value, expires_at = fetch_fn()
            _set_local(cache_key, value, expires_at)
            return value


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def get_kis_access_token() -> str:
    """KIS REST 접근토큰 반환 (캐시 유효 시 캐시, 아니면 재발급)."""
    return _get_cached_or_refresh(_REST_TOKEN_KEY, _REST_LOCK_KEY, _fetch_access_token)


def get_kis_approval_key() -> str:
    """KIS 웹소켓 접속키 반환 (캐시 유효 시 캐시, 아니면 재발급)."""
    return _get_cached_or_refresh(_WS_TOKEN_KEY, _WS_LOCK_KEY, _fetch_approval_key)