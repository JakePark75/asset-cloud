"""
price_updater.py — 런처
config.json 의 interval 값에 따라 REST / 웹소켓 모드를 분기한다.
  interval = 0   →  price_updater_ws.py    (웹소켓 실시간 방식)
  interval > 0   →  price_updater_rest.py  (REST 폴링 방식, interval분 주기)

설정 변경 시 systemctl restart price_updater 로 즉시 반영.
systemd 서비스는 이 파일을 그대로 바라본다.
"""
import json
import os
import sys

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_mode() -> bool:
    """config.json 에서 interval 값을 읽어 웹소켓 모드 여부 반환. interval=0이면 True."""
    if not os.path.exists(CONFIG_FILE):
        print(f"[ERROR] config.json 없음: {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    return int(cfg.get("interval", 1)) == 0


if __name__ == "__main__":
    realtime = load_mode()

    if realtime:
        print("[launcher] interval=0 → 웹소켓 모드 시작")
        from price_updater_ws import main
    else:
        print("[launcher] interval>0 → REST 폴링 모드 시작")
        from price_updater_rest import main

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.force and not realtime:
        # --force 는 REST 모드에서만 지원
        from price_updater_rest import load_config, run_update_cycle, log
        load_config()
        log.info("강제 업데이트 실행 (--force)")
        run_update_cycle(force=True)
    elif args.force and realtime:
        # ws 모드에서 --force는 의미없음 — 좀비 프로세스 방지를 위해 즉시 종료
        print("[launcher] ws 모드에서 --force 무시 — 이미 실시간 업데이트 중", file=sys.stderr)
        sys.exit(0)
    else:
        main()