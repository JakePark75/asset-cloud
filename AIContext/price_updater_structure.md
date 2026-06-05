# price_updater.py — 구조 요약

## 파일 위치
`scheduler/price_updater.py`

## 설정 파일
`scheduler/config.json`
- `kis_app_key` / `kis_app_secret`: KIS API 인증
- `db_password`: PostgreSQL 접속 비밀번호
- `interval`: 업데이트 주기 (분, 기본값 1) — 실행 중 변경 즉시 반영
- `kr_holiday_api_key`: 한국 공휴일 조회 API 키
- `us_holiday_api_key`: 미국 공휴일 조회 API 키

## 전역 상태
```
config            # config.json 내용
access_token      # KIS API 토큰 캐시
token_expires     # 토큰 만료 시각 (unix timestamp)
token_lock        # 멀티스레드 토큰 공유용 Lock
holiday_cache     # HolidayCache 전역 인스턴스
_close_confirmed  # {"KR": date, "US": date} — 당일 종가 확정된 시장 기록
_close_lock       # _close_confirmed 멀티스레드 보호용 Lock
```

---

## 메인 흐름

```
main()
  └─ while True:
       load_config()                      # 매 사이클마다 config.json 재로드
       holiday_cache.refresh_if_needed()  # 매일 08:00 KST에 1회 공휴일 갱신
       run_update_cycle()                 # 전체 종목 업데이트
       sleep(interval - elapsed)         # 남은 시간 대기
```

```
run_update_cycle()
  ├─ tickers 테이블 전체 조회 (ticker, market)
  ├─ force=True: 전체 종목 실시간 조회
  └─ force=False:
       ├─ status == "open"  → targets (실시간 조회)
       ├─ status == "after" → targets + close_targets (실시간 + 종가확정 병행)
       │                       단, _is_close_confirmed() == True면 완전 스킵
       └─ status == "closed" → 스킵
       종목별 threading.Thread 병렬 실행 (모든 스레드 join 후 완료)
       완료 후 NOTIFY price_updated 전송
```

```
update_worker(row)
  ├─ market == "KR"                → get_kr_price()
  ├─ market in NAS/NYS/AMS/ARC    → get_us_price()
  ├─ market in FX/INDEX/CRYPTO    → get_yahoo_price()
  ├─ price == 0 이면 DB 업데이트 건너뜀
  └─ update_ticker_in_db()
```

```
close_confirm_worker(row)
  ├─ market == "KR"             → get_confirmed_close_kr()
  ├─ market in NAS/NYS/AMS/ARC → get_confirmed_close_us()
  ├─ result == None → 미확정, 종료
  └─ result 있으면 update_ticker_in_db() + _set_close_confirmed(group)
```

---

## 함수 목록

### load_config()
- config.json 읽어서 config 전역변수에 저장
- 파일 없으면 FileNotFoundError

### get_db_conn()
- psycopg2로 assetdb 접속 (host=localhost, user=jake)
- 호출마다 새 커넥션 반환 (종목별로 열고 닫음)

### get_access_token()
- KIS API OAuth 토큰 발급
- token_lock으로 멀티스레드 안전하게 처리
- 만료 전이면 캐시 재사용, 만료 시 재발급

### HolidayCache (클래스)
- 매일 08:00 KST에 한국/미국 공휴일을 외부 API로 조회 후 캐싱
- `refresh_if_needed()`: main() 루프에서 매 interval마다 호출, 당일 첫 호출 시 1회만 갱신
- `is_kr_holiday(date)`: 한국 공휴일 여부
- `is_us_holiday(date)`: 미국 공휴일 여부

### get_market_status(market) → str
- 시장 상태 반환: `"open"` / `"pre"` / `"after"` / `"closed"`
- KR: 09:00~15:30 → open, 15:30~18:00 → after, 그 외 → closed
- US(NAS/NYS/AMS/ARC): 04:00~16:00 → open (프리마켓 포함), 16:00~20:00 → after, 그 외 → closed
- FX/CRYPTO/INDEX: 항상 "open" (24시간)
- 주말/공휴일: closed

### is_market_open(market) → bool
- 하위 호환용 래퍼. `get_market_status(market) == "open"` 과 동일
- settings.py 등 외부에서 기존 코드 호환용으로 유지

### get_kr_price(ticker)
- KIS API 국내주식 현재가 조회
- 장마감 시 전일종가(prdy_clpr)로 fallback
- 반환: (price, change_pct)

### get_us_price(ticker, excd)
- KIS API 미국주식 현재가 조회
- excd: NAS / NYS / AMS / ARC
- last 없으면 base(전일종가)로 fallback
- 반환: (price, change_pct)

### get_yahoo_price(ticker)
- Yahoo Finance chart API 직접 호출
- 지수, 환율(USDKRW=X, JPYKRW=X 등), 암호화폐(BTC-KRW 등) 포함
- regularMarketPrice: 실시간 현재가
- regularMarketTime: 실제 데이터 시각 → data_time으로 DB 저장
- 반환: (price, change_pct, data_time)

### get_confirmed_close_kr(ticker)
- KIS API 일별시세(FHKST03010100)로 당일 종가 확정 여부 확인
- date_1 = date_2 = 오늘 날짜로 조회
- output2 비어있으면 미확정 → None 반환
- output2에 오늘 날짜 데이터 있으면 (price, change_pct) 반환

### get_confirmed_close_us(ticker, excd)
- KIS API 해외주식 일별시세(HHDFS76240000)로 당일 종가 확정 여부 확인
- output2[0].xymd == 오늘 AND tvol != "0" 이면 확정
- 미확정이면 None 반환, 확정이면 (price, change_pct) 반환

### _is_close_confirmed(market_group) → bool
- 오늘 날짜로 해당 그룹(KR/US) 종가 확정 여부 반환

### _set_close_confirmed(market_group)
- 종가 확정 플래그 세팅 — 이후 해당 그룹 조회 중단

### _market_group(market) → str
- market → market_group 매핑 (KR→"KR", NAS/NYS/AMS/ARC→"US")

### update_ticker_in_db(conn, ticker, price, change_pct, data_time=None)
- tickers 테이블 UPDATE
- 업데이트 컬럼: current_price, change_pct, updated_at(현재시각), data_time
- data_time은 FX/INDEX/CRYPTO만 값 있음, 나머지는 NULL

---

## systemd 서비스
- 서비스명: price_updater
- 실행 유저: ubuntu
- 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작