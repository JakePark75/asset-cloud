"""
common/notify.py
텔레그램 이상 알림 (더미 — 추후 실제 텔레그램 연동으로 교체).

원래 scheduler/daily_inserter.py에 있던 함수를 이곳으로 옮겼다.
이유: app.utils.snap / app.utils.daily_snapshot 이 이 함수를 쓰려고
scheduler.daily_inserter를 역참조(import)하면서 순환 임포트가 생겼었음
(daily_inserter -> daily_snapshot -> daily_inserter,
 daily_inserter -> snap -> daily_inserter).
이 모듈은 다른 프로젝트 모듈에 의존하지 않는 leaf 모듈이므로,
세 파일 모두 여기서 가져다 쓰면 순환고리가 완전히 사라진다.
"""

import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def notify_telegram_alert(message: str) -> None:
    """
    이상상황(디버깅 필요) 발생 시 텔레그램 알림 발송.
    TODO: 실제 텔레그램 봇 연동 구현 예정. 현재는 로그만 출력.
    """
    now_kst = datetime.datetime.now(KST)
    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST] "
          f"📨 [TELEGRAM DUMMY] {message}", flush=True)