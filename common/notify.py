"""
common/notify.py
텔레그램 이상 알림.

원래 scheduler/daily_inserter.py에 있던 함수를 이곳으로 옮겼다.
이유: app.utils.snap / app.utils.daily_snapshot 이 이 함수를 쓰려고
scheduler.daily_inserter를 역참조(import)하면서 순환 임포트가 생겼었음
(daily_inserter -> daily_snapshot -> daily_inserter,
 daily_inserter -> snap -> daily_inserter).
이 모듈은 다른 프로젝트 모듈에 의존하지 않는 leaf 모듈이므로,
세 파일 모두 여기서 가져다 쓰면 순환고리가 완전히 사라진다.

실제 텔레그램 연동 (2026-07-08 세션):
- config.json 경로는 common/kis_auth.py와 동일한 방식(Path(__file__).parent.parent
  / "scheduler" / "config.json")으로 찾는다 - 이미 있는 패턴을 그대로 따름.
- 전송 실패(네트워크 오류, 잘못된 토큰 등)는 예외를 삼키고 로그만 남긴다.
  이 함수는 이미 완료된 작업(DB 반영, 로그 파일 저장) 뒤에 부가적으로 호출되므로,
  알림 전송 실패가 호출한 쪽의 파이프라인을 중단시키면 안 되기 때문.
"""

import datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"
TELEGRAM_API_TIMEOUT_SEC = 10

log = logging.getLogger("notify")


def _load_telegram_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        raise KeyError(f"{CONFIG_PATH}에 telegram_token / telegram_chat_id 없음")
    return token, chat_id


def notify_telegram_alert(message: str) -> None:
    """
    이상상황(디버깅 필요) 발생 시 텔레그램 알림 발송.
    """
    now_kst = datetime.datetime.now(KST)
    timestamp = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp} KST] {message}"

    try:
        token, chat_id = _load_telegram_config()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": full_message},
            timeout=TELEGRAM_API_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            log.error(f"텔레그램 전송 실패(API 응답 ok=false): {result}")
    except Exception as e:
        log.error(f"텔레그램 전송 실패: {e}")

    # 콘솔/로그에도 항상 남김 (전송 성공/실패와 무관, 기존 동작 유지)
    print(f"📨 [TELEGRAM] {full_message}", flush=True)