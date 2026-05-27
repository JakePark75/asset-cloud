# price_updater.py — 구조 요약

## 파일 위치
`scheduler/price_updater.py`

## 설정 파일
`scheduler/config.json`
- `kis_app_key` / `kis_app_secret`: KIS API 인증
- `db_password`: PostgreSQL 접속 비밀번호
- `interval`: 업데이트 주기 (분, 기본값 1) — 실행 중 변경 즉시 반영

## 전역 상태
```
config        # config.json 내용
access_token  # KIS API 토큰 캐시
token_expires # 토큰 만료 시각 (unix timestamp)
token_lock    # 멀티스레드 토큰 공유용 Lock
```

---

## 메인 흐름

```
main()
  └─ while True:
       load_config()               # 매 사이클마다 config.json 재로드
       run_update_cycle()          # 전체 종목 업데이트
       sleep(interval - elapsed)  # 남은 시간 대기
```

```
run_update_cycle()
  ├─ tickers 테이블 전체 조회 (ticker, market)
  └─ 종목별 threading.Thread → update_worker() 병렬 실행
       (모든 스레드 join 후 완료)
```

```
update_worker(row)
  ├─ market == "KR"                → get_kr_price()
  ├─ market in NAS/NYS/AMS/ARC    → get_us_price()
  ├─ market == "IDX"              → get_index_price()
  ├─ price == 0 이면 DB 업데이트 건너뜀
  └─ update_ticker_in_db()
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

### get_kr_price(ticker)
- KIS API 국내주식 현재가 조회
- 장마감 시 전일종가(prdy_clpr)로 fallback
- 반환: (price, change_pct)

### get_us_price(ticker, excd)
- KIS API 미국주식 현재가 조회
- excd: NAS / NYS / AMS / ARC
- last 없으면 base(전일종가)로 fallback
- 반환: (price, change_pct)

### get_index_price(ticker)
- Yahoo Finance chart API 직접 호출
- 지수, 환율(USDKRW=X, JPYKRW=X 등), 암호화폐(BTC-KRW 등) 포함
- regularMarketPrice: 실시간 현재가
- regularMarketTime: 실제 데이터 시각 → data_time으로 DB 저장
- 반환: (price, change_pct, data_time)

### update_ticker_in_db(conn, ticker, price, change_pct, data_time=None)
- tickers 테이블 UPDATE
- 업데이트 컬럼: current_price, change_pct, updated_at(현재시각), data_time
- data_time은 IDX만 값 있음, 나머지는 NULL

---

## systemd 서비스
- 서비스명: price_updater
- 실행 유저: ubuntu
- 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작