# stock_updater.py — 구조 요약

## 전역 변수 / 상태

```
config              = {}        # config.json 내용 (app_key, app_secret, telegram_token 등)
status              = {}        # last_update, last_status, error, last_heartbeat, last_telegram_msg_id
access_token        = None      # KIS API 액세스 토큰 캐시
token_expires       = 0         # 토큰 만료 시각 (unix timestamp)
tray_icon           = None      # pystray Icon 전역 참조
is_in_office        = False     # 사무실 모드 (True이면 텔레그램 전송 안 함)

update_event        = Event()   # set() 하면 즉시 업데이트 루프 실행
historical_mode_evt = Event()   # set() 상태면 실시간 업데이트 중단, 과거 조회 모드
work_stop_evt       = Event()   # set() 하면 현재 진행 중인 루프 조기 중단 요청
work_stopped_evt    = Event()   # 루프가 중단 완료되면 set()

BASE_DIR    # 실행파일 기준 디렉토리
CONFIG_FILE # BASE_DIR/config.json
LOG_FILE    # BASE_DIR/stock_updater.log
STATE_FILE  # BASE_DIR/state.json (last_telegram_msg_id 영속)
```

---

## 함수 목록

### [유틸리티]

```
get_visual_width(text)
    # 한글=2칸, 영문/숫자=1칸으로 텍스트 시각적 너비 계산
    # 텔레그램 텍스트 리포트 컬럼 정렬에 사용

escape_md(text)
    # 텔레그램 MarkdownV2 특수문자 이스케이프 처리
```

### [설정/상태 파일 I/O]

```
load_config()
    # config.json 읽어서 config 전역변수에 반환
    # 파일 없으면 sys.exit(1)
    # interval 기본값 3 세팅

save_config()
    # 현재 config 전역변수를 config.json에 저장

load_state()
    # state.json에서 last_telegram_msg_id 읽어서 status에 복원

save_state()
    # status의 last_telegram_msg_id를 state.json에 저장
```

### [엑셀 연동]

```
open_excel_and_get_sheet()
    # win32com으로 Excel 연결 (이미 열려있으면 재사용, 없으면 새로 오픈)
    # config의 excel_file, sheet_name 기준
    # 반환: (xl, wb, ws)

find_columns(ws)
    # 시트 1~5행 스캔해서 헤더 컬럼 위치 탐색
    # 탐색 대상: 티커, 현재가, 등락률, 업데이트시간, 구분, 데이터시간
    # 반환: (header_row, col_ticker, col_price, col_change, col_time, col_div, col_data_time)
```

### [시세 수집 API]

```
get_access_token()
    # KIS API OAuth 토큰 발급 (캐시 처리, 만료 전 재사용)
    # POST https://openapi.koreainvestment.com:9443/oauth2/tokenP

get_kr_price(ticker)
    # KIS API로 국내주식 현재가 조회
    # 장 마감 시 전일 종가로 fallback
    # 반환: (price, change_pct)

get_us_price(ticker, excd)
    # KIS API로 미국주식 현재가 조회 (excd: NAS/NYS/AMS/ARC)
    # 반환: (price, change_pct)

get_fx_rate(ticker)
    # open.er-api.com으로 환율 조회 (ticker 형식: "USD/KRW")
    # 반환: (rate, 0)

get_index_price(ticker)
    # Yahoo Finance로 지수 현재가 조회 (^IXIC, ^NDX 등)
    # 반환: (price, change_pct, data_time)
```

### [텔레그램 전송]

```
delete_telegram_message()
    # status의 last_telegram_msg_id 메시지를 텔레그램에서 삭제
    # 새 메시지 전송 전 항상 호출됨 (채팅방 깔끔하게 유지)

send_telegram_msg(message)
    # MarkdownV2 텍스트 메시지 전송 (알림 없음)
    # 전송 전 delete_telegram_message() 호출
    # 전송 성공 시 last_telegram_msg_id 갱신 + save_state()

send_telegram_photo(image_path, caption)
    # 이미지 파일을 텔레그램으로 전송 (알림 없음)
    # 전송 전 delete_telegram_message() 호출
    # 전송 성공 시 last_telegram_msg_id 갱신 + save_state()
```

### [리포트 생성]

```
send_status_report(ws_inst)
    # 업데이트 완료 후 호출되는 리포트 발송 메인 함수
    # 1순위: create_report_image() → send_telegram_photo()
    # fallback: 텍스트 리포트 생성 → send_telegram_msg()
    # is_in_office=True 이면 이미지 생성만 하고 전송 안 함
    # 데이터 출처: ws_inst.Range("A1").Value 에 적힌 범위의 셀 값

create_report_image(ws_inst, output_path)
    # Pillow로 PNG 리포트 이미지 생성
    # 3개 카드 구조: 카드1(자산요약) / 카드2(투자현황) / 카드3(수익률지표)
    # 데이터 출처: ws_inst.Range("A1").Value 에 적힌 범위
    # 폰트: malgun.ttf / malgunbd.ttf (맑은 고딕)
    # 내부 헬퍼: parse_pct / amount_only / to_eok / parse_x3x2x1
    # 내부 드로잉 유틸: tw / th / txt / rrect / hline / vline / badge_draw
    # 내부 아이콘: draw_icon_wallet / draw_icon_donut / draw_icon_chart / draw_icon_bars

draw_card(draw, x1, y1, x2, y2)
    # 카드 배경 둥근 사각형 그리기 (현재 create_report_image 내부로 흡수되어 미사용)

_get_icon_image_from_config(config_key)
    # config에 등록된 경로에서 PIL 이미지 로드
    # 실패 시 투명 빈 이미지 반환 (프로그램 중단 방지)
    # 현재 실제 호출 없음 (미사용 상태)

export_range_to_png(ws, rng_address, output_path)
    # 엑셀 범위를 ChartObjects 경유해서 PNG로 내보냄
    # 현재 실제 호출 없음 (미사용 상태)
```

### [과거 데이터 조회]

```
get_historical_price_yahoo(ticker, date_str)
    # Yahoo Finance에서 특정일 종가 조회 (FX/INDEX/CRYPTO, 미국주식용)
    # date_str 형식: "YYYY-MM-DD"
    # 주말/공휴일 대비 +4일 범위로 조회 후 가장 가까운 날짜 반환

get_historical_price_kr(ticker, date_str)
    # KIS API로 국내주식 특정일 종가 조회
    # date_str 형식: "YYYY-MM-DD"

enter_historical_mode(date_str, ws)
    # 과거 데이터 조회 모드 진입
    # 실행 전 work_stop_evt로 현재 실시간 루프 중단 대기
    # 시트 전체 종목을 순회하며 해당 날짜 종가로 업데이트
    # 완료 후 work_stopped_evt.set()

exit_historical_mode()
    # 실시간 모드로 복귀
    # historical_mode_evt.clear() + update_event.set()으로 즉시 재개
```

### [일간 누적]

```
insert_daily_row()
    # 자산변동 시트(daily_sheet_name)에 오늘 행 삽입
    # A1 셀 값으로 샘플행 위치 결정
    # 순서: 샘플행 위에 빈 행 삽입 → 샘플행 수식 복사 → 샘플행 값으로 고정 → 색상 적용
    # 트레이 메뉴에서 수동 실행
```

### [메인 루프]

```
update_loop()
    # 백그라운드 스레드로 실행되는 메인 업데이트 루프
    # historical_mode_evt가 set이면 대기
    # 동작: open_excel_and_get_sheet → find_columns → 종목 순회 → 시세 업데이트
    # 구분(div)별 API 분기: KR/NAS/AMS/ARC/FX/INDEX/CRYPTO
    # 완료 후 send_status_report() 호출
    # config.interval(분) 대기 후 반복
    # ws 오류 시 Excel 재연결
```

### [시스템 트레이]

```
make_icon(state)
    # 트레이 아이콘 PIL 이미지 생성
    # state: "normal"(초록) / "error"(빨강) / "hang"(노랑)

quit_app(icon, item)
    # 트레이 종료 메뉴 핸들러, os._exit(0)

restart_app(icon, item)
    # 프로세스 재시작 (os.execl)

toggle_in_office(icon, item)
    # is_in_office 토글 + 메뉴 갱신

run_tray()
    # 트레이 아이콘 및 메뉴 구성 후 실행 (메인 스레드 점유)
    # 내부 함수:
    #   is_checked(min_val)       : 주기 메뉴 체크 상태
    #   set_interval(icon, item)  : 주기 변경 + save_config + 즉시 업데이트
    #   open_historical_mode()    : tkinter 날짜 입력창 → enter_historical_mode() 스레드 실행
    # watchdog 스레드: 5초마다 last_heartbeat 체크
    #   300초 이상 → hang 아이콘
    #   600초 이상 → restart_app
```

### [진입점]

```
if __name__ == "__main__":
    # load_config() → load_state()
    # update_loop를 daemon 스레드로 시작
    # run_tray() (메인 스레드)
```
