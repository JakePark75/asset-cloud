# price_updater — 구조 요약

## 파일 구성

| 파일 | 역할 |
|------|------|
| `scheduler/price_updater.py` | 런처 — config.json의 interval 값에 따라 REST/WS 모드 분기 |
| `scheduler/price_updater_common.py` | 공통 — 설정 로드, DB 연결, 시장 상태, 공휴일 캐시, Yahoo 시세, Redis 시세 업데이트 |
| `scheduler/price_updater_rest.py` | REST 폴링 모드 — interval > 0일 때 동작, N분 주기 전 종목 조회 |
| `scheduler/price_updater_ws.py` | 웹소켓 모드 — interval = 0일 때 동작, KIS WS 실시간 수신 |

## 설정 파일
`scheduler/config.json`
- `kis_app_key` / `kis_app_secret`: KIS API 인증
- `db_password`: PostgreSQL 접속 비밀번호
- `interval`: 업데이트 주기 (분) — `0`이면 웹소켓 모드, `1` 이상이면 REST 폴링 모드
- `data_go_kr_key`: 한국 공휴일 조회 API 키
- `finnhub_api_key`: 미국 공휴일 조회 API 키 (Finnhub)

---

## price_updater.py — 런처

```
if interval == 0 → price_updater_ws.main()
if interval  > 0 → price_updater_rest.main()
--force 옵션: REST 모드에서만 지원 (run_update_cycle(force=True) 1회 실행 후 종료)
```

---

## price_updater_common.py — 공통 모듈

### 전역 상태
```
config            # config.json 내용
access_token      # KIS API REST 토큰 캐시
token_expires     # 토큰 만료 시각 (unix timestamp)
token_lock        # 멀티스레드 토큰 공유용 Lock
holiday_cache     # HolidayCache 전역 인스턴스
_close_confirmed  # {"KR": date} — 당일 종가 확정된 시장 기록
_close_lock       # _close_confirmed 멀티스레드 보호용 Lock
```

### 함수 목록

#### load_config()
- config.json 읽어서 config 전역변수에 저장
- 파일 없으면 FileNotFoundError

#### get_db_conn()
- psycopg2로 assetdb 접속 (host=localhost, user=jake)
- 호출마다 새 커넥션 반환 (종목별로 열고 닫음)

#### HolidayCache (클래스)
- 매일 08:00 KST에 한국/미국 공휴일을 외부 API로 조회 후 캐싱
- `refresh_if_needed()`: 당일 첫 호출 시 1회만 갱신 (08:00 KST 이후)
- `is_kr_holiday(date)`: 한국 공휴일 여부
- `is_us_holiday(date)`: 미국 공휴일 여부
- 한국: 공공데이터포털 특일 API (`data_go_kr_key`)
- 미국: Finnhub market-holiday API (`finnhub_api_key`)

#### get_market_status(market) → str
 반환값: `"open"` / `"pre"` / `"after"` / `"closed"`

#### is_market_open(market) → bool
- 하위 호환용 래퍼. `get_market_status(market) == "open"` 과 동일

#### get_access_token()
- KIS API OAuth REST 토큰 발급 (웹소켓 접속키와 별개)
- token_lock으로 멀티스레드 안전 처리
- 만료 전이면 캐시 재사용

#### get_yahoo_price(ticker)
- Yahoo Finance chart API 직접 호출
- 지수, 환율(USDKRW=X 등), 암호화폐(BTC-KRW 등) 포함
- 반환: (price, change_pct, data_time)

#### update_price_cache(ticker, price, change_pct, data_time=None)
- Redis `prices` hash 갱신
- FX/INDEX/CRYPTO는 `data_time`을 쓰지만, 실제 Redis 저장 함수는 `price`와 `change_pct`만 기록
- data_time은 설명용 값이며, Redis에 별도 저장되지 않는다

#### _is_close_confirmed(market_group) → bool
- 오늘 날짜로 해당 그룹(KR) 종가 확정 여부 반환

#### _set_close_confirmed(market_group)
- 종가 확정 플래그 세팅 — 이후 해당 그룹 조회 중단

#### _market_group(market) → str
- market → market_group 매핑 (KR→"KR")

---

## price_updater_rest.py — REST 폴링 모드

### 메인 흐름

```
main()
  └─ while True:
       load_config()                      # 매 사이클마다 config.json 재로드
       holiday_cache.refresh_if_needed()  # 매일 08:00 KST에 1회 공휴일 갱신
       run_update_cycle()
       sleep(interval_sec - elapsed)
```

```
run_update_cycle(force=False)
  ├─ tickers 테이블 전체 조회 (ticker, market)
  ├─ force=True: 전체 종목 실시간 조회
  └─ force=False:
       ├─ status in open/pre/after  → targets (실시간 조회)
       └─ status == "closed"        → 스킵
       종목별 threading.Thread 병렬 실행 (모든 스레드 join 후 완료)
       완료 후 Redis pub/sub `publish_price_updated()` 전송
```

```
update_worker(row)
  ├─ market == "KR"             → get_kr_price()
  ├─ market in NAS/NYS/AMS/ARC → get_us_price()
  ├─ market in FX/INDEX/CRYPTO → get_yahoo_price()
  ├─ price == 0 이면 Redis 업데이트 건너뜀
  └─ update_price_cache()
```

```
KR final close
  ├─ `should_run_kr_final_close()`로 하루 1회 실행 여부 판정
  └─ 확정 시 `run_kr_final_close_update()` 호출 — Redis 시세 갱신, `recalc_today_row()`, `publish_price_updated()` 실행
```

### 함수 목록

#### get_kr_price(ticker)
- KIS API 국내주식 현재가 조회 (FHKST01010100)
- 장마감 시 전일종가(prdy_clpr)로 fallback
- 반환: (price, change_pct)

#### get_us_price(ticker, excd)
- KIS API 미국주식 현재가 조회 (HHDFS00000300)
- excd: NAS / NYS / AMS / ARC
- last 없으면 base(전일종가)로 fallback
- 반환: (price, change_pct)

#### get_confirmed_close_kr(ticker)
- KIS API 일별시세(FHKST03010100)로 당일 종가 확정 여부 확인
- output2 비어있거나 오늘 날짜 아니면 None 반환
- 확정이면 (price, change_pct) 반환

---

## price_updater_ws.py — 웹소켓 모드

### 개요
- KR/US 종목: KIS 웹소켓 (H0STCNT0 / HDFSCNT0) push 수신
- FX/INDEX/CRYPTO: Yahoo Finance REST 폴링 (별도 asyncio task, 10초 주기)
- asyncio 기반 (asyncio.gather로 태스크 병렬 실행)

### 상수
```
WS_URL = "ws://ops.koreainvestment.com:21000"
US_MARKET_PREFIX = {"NAS": "DNAS", "NYS": "DNYS", "AMS": "DAMS", "ARC": "DARC"}
KR_IDX_PRICE = 2, KR_IDX_CHANGE_PCT = 5      # H0STCNT0 필드 인덱스
US_IDX_PRICE = 11, US_IDX_CHANGE_PCT = 14    # HDFSCNT0 필드 인덱스
YAHOO_POLL_INTERVAL = 10                      # Yahoo 폴링 주기 (초)
WS_RECONNECT_DELAY = 10                       # 재연결 대기 (초)
```

### 메인 흐름

```
main()
  ├─ load_config()
  ├─ holiday_cache.refresh_if_needed()
  ├─ get_subscribe_targets() → kr_tickers, us_rows, yahoo_rows
  ├─ get_approval_key()       # KIS 웹소켓 전용 접속키 발급 (REST 토큰과 별개)
  └─ asyncio.run(run())
       ├─ kis_ws_task()           # KR/US 웹소켓 수신
       ├─ yahoo_poll_task()       # FX/INDEX/CRYPTO REST 폴링
       └─ subscription_refresh_task()  # 5분마다 구독 대상 변경 감지
```

```
kis_ws_task(approval_key, kr_tickers, us_rows)
  └─ while True:
       websockets.connect(WS_URL)
       ├─ KR 종목 H0STCNT0 구독
       ├─ US 종목 HDFSCNT0 구독 (tr_key = prefix + ticker)
       └─ async for raw_msg:
            ├─ "PINGPONG" → 응답 전송
            ├─ JSON → 구독 결과 로그
            └─ "|" 구분 데이터: 형식 = 0|tr_id|data_cnt|data (parts[1]=tr_id, parts[3]=데이터)
                 ├─ H0STCNT0 → parse_kr() → _save_price()
                 └─ HDFSCNT0 → parse_us() → tr_key_map 역매핑 → _save_price()
            약 0.2초 주기마다 _notify()
      연결 끊기면 WS_RECONNECT_DELAY 후 재연결
```

```
yahoo_poll_task(yahoo_rows)
  └─ while True:
       FX/INDEX/CRYPTO 종목별 get_yahoo_price() → update_price_cache()
       _notify()
       await asyncio.sleep(10)
```

```
subscription_refresh_task(approval_key_holder)
  └─ while True:
       await asyncio.sleep(300)   # 5분 대기
       get_subscribe_targets() 재조회
       구독 대상 변경 감지 시 → os.execv()로 프로세스 재시작 (systemd Restart=always 활용)
```

### 함수 목록

#### get_approval_key() → str
- KIS 웹소켓 전용 접속키 발급 (`/oauth2/Approval`)
- REST 토큰(`get_access_token()`)과 별개

#### make_us_tr_key(ticker, market) → str
- US 종목의 웹소켓 tr_key 생성: `US_MARKET_PREFIX[market] + ticker`
- 예: TQQQ (NAS) → "DNASTQQQ"

#### get_subscribe_targets() → (kr_tickers, us_rows, yahoo_rows)
- DB tickers 전체 조회 후 get_market_status()에 따라 분류
- KR: open → kr_tickers
- US: open/pre/after → us_rows
- FX/INDEX/CRYPTO: 항상 → yahoo_rows

#### parse_kr(raw) → (ticker, price, change_pct) | None
- H0STCNT0 수신 데이터(`^` 구분) 파싱

#### parse_us(raw) → (symb, price, change_pct) | None
- HDFSCNT0 수신 데이터(`^` 구분) 파싱
- symb에는 prefix 포함 (예: "DNASTQQQ") — tr_key_map으로 역매핑

#### _us_tr_key_to_ticker(tr_key, us_ticker_set) → str | None
- prefix 4자리 제거 후 us_ticker_set에서 ticker 확인

#### _save_price(ticker, price, change_pct)
- update_price_cache()로 Redis `prices` hash 갱신
- price == 0이면 스킵

#### _notify()
- `recalc_today_row()` 실행 후 Redis pub/sub `publish_price_updated()` 전송

---

## systemd 서비스
- 서비스명: price_updater
- 실행 유저: ubuntu
- 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작
- 웹소켓 모드에서 구독 대상 변경 시 os.execv()로 자체 재시작 (systemd가 재기동)