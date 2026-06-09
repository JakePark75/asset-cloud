# 개인 자산관리 시스템 — 클라우드 전환 프로젝트

---

## 1. 프로젝트 개요

### 목적
로컬 PC에서 엑셀 + 파이썬으로 운영 중인 개인 자산 관리 시스템을 Oracle Cloud 기반으로 재구현한다.

### 1차 목표
기존 로컬 시스템의 핵심 기능을 클라우드에서 동일하게 동작하도록 재현한다.

### 이후 목표
다양한 기능 추가 및 성능 개선.

---

## 2. 개발 스택 및 인프라

| 항목 | 내용 |
|------|------|
| VM | Oracle Cloud 도쿄 리전 (161.33.151.220) |
| OS | Ubuntu |
| Python | 3.10.12 |
| 도메인 | myassets.mooo.com |
| DB | PostgreSQL (VM에 직접 설치) |
| 프레임워크 | Shiny for Python 1.6.2 |
| 리포지토리 | https://github.com/JakePark75/asset-cloud |
| CSS | app/static/style.css | 다크테마 공통 스타일, 인라인 style 지양하고 클래스로 관리 |

### 주요 패키지 버전
| 패키지 | 버전 | 용도 |
|--------|------|------|
| shiny | 1.6.2 | 웹 프레임워크 |
| psycopg2 | 2.9.12 | PostgreSQL 동기 연결 (DB 조회/수정) |
| asyncpg | 0.31.0 | PostgreSQL 비동기 연결 (LISTEN/NOTIFY) |
| websockets | - | KIS 웹소켓 실시간 시세 수신 |

### Shiny 1.6.2 주의사항
- `App()`의 `lifespan` 파라미터 미지원
- 백그라운드 태스크 시작은 `asyncio.get_event_loop().create_task()`를 server 함수 진입부에서 호출하는 방식 사용
- DB 타입 주의: psycopg2로 조회한 NUMERIC 컬럼은 decimal.Decimal 타입으로 반환됨. Python float과 연산 시 타입 오류 발생 → 필요 시 float() 명시 변환 필요.

### 서버 구조
- **nginx**: 활성화 (systemd), 443/80 리스닝, SSL은 Certbot으로 관리
  - `/` → `http://127.0.0.1:8080` 프록시 (WebSocket 포함)
  - `/api/` → `http://127.0.0.1:8080/api/` 프록시
  - `/AIContext/` → `/var/www/html` 정적 파일 서빙
  - `proxy_read_timeout 3600`, `proxy_send_timeout 3600` 설정 (WebSocket 끊김 방지)
- **Shiny 앱**: systemd 서비스로 실행 (`/etc/systemd/system/myassets.service`)
  - 실행 커맨드: `python3 -m shiny run app/app.py --host 0.0.0.0 --port 8080`
  - 서비스 파일 원본: `scheduler/myassets.service` (깃허브 관리)
- **price_updater**: systemd 서비스로 실행 (`/etc/systemd/system/price_updater.service`)
  - 서비스 파일 원본: `scheduler/price_updater.service` (깃허브 관리)

### 프로젝트 디렉토리 구조

> 각 파일의 상세 함수/로직은 `AIContext/` 하위 구조 문서 참조.
> 유틸 함수 상세는 `utils_structure.md` 참조.

```
/home/ubuntu/asset-cloud/
├── AIContext/           # AI 컨텍스트 MD 파일 (nginx 정적 서빙)
│   ├── project_status.md
│   ├── utils_structure.md       # ★ app/utils 유틸 함수 상세 (metrics, daily_snapshot, snap)
│   ├── app_structure.md
│   ├── accounts_structure.md
│   ├── history_structure.md
│   ├── dashboard_structure.md   # ★ 대시보드 구조 (Bloomberg 스타일, SVG 차트)
│   ├── portfolio_structure.md
│   ├── settings_structure.md
│   ├── scheduler_structure.md
│   └── price_updater_structure.md
├── README.md
├── app/
│   ├── app.py           # 진입점 (Shiny App + Starlette 라우팅, 하단 탭바, 로그인)
│   │                    # → 상세: app_structure.md
│   ├── auth.py          # 로그인 인증
│   │                    # verify_login() / create_token() / verify_token()
│   ├── db.py            # DB 연결 공통
│   │                    # get_db() / get_config() / get_usd_krw() / save_config()
│   │                    # get_market_map() / get_market_currency() / get_market_label()
│   │                    # is_us_market() / get_supported_markets()
│   ├── context_api.py   # AI 컨텍스트 MD 서빙 API
│   ├── price_signal.py  # 실시간 시세 갱신 신호 (LISTEN/NOTIFY)
│   │                    # price_signal.get() 호출로 렌더러에 의존성 등록
│   ├── static/
│   │   ├── style.css    # 공통 스타일 (다크테마, 인라인 style 지양)
│   │   └── dashboard.css  # 대시보드 전용 스타일 (Bloomberg 다크테마, JetBrains Mono 폰트)
│   ├── utils/           # ★ 순수 유틸 — DB/화면 의존 없음. 상세: utils_structure.md
│   │   ├── metrics.py         # 순수 계산 함수
│   │   │                      # to_f() / calculate_exposure_and_ratios()
│   │   │                      # calculate_xirr() / calculate_monthly_irr()
│   │   │                      # calculate_alpha() / calculate_beta()
│   │   │                      # calculate_daily_profit() / calculate_retirement_asset()
│   │   ├── daily_snapshot.py  # 단일 날짜 스냅샷 계산 → dict 반환 (daily_inserter에서 사용)
│   │   │                      # get_daily_snapshot(target_date) → dict
│   │   └── snap.py            # 날짜 범위 스냅샷 계산 (캐시 최적화, 누락 보정용)
│   │                          # fetch_snapshot() / fetch_positions() / date_range()
│   │                          # _GLOBAL_START_DATE_STR / _GLOBAL_END_DATE_STR 세팅 필요
│   └── modules/
│       ├── components.py            # 공통 포맷 유틸 + 공통 UI 컴포넌트
│       │                            # fmt_krw() / fmt_usd() / fmt_pct() / fmt_pnl() / fmt_change()
│       │                            # render_summary_header()
│       ├── dashboard.py             # 대시보드 UI/server (Bloomberg 스타일 재작성) → dashboard_structure.md
│       ├── portfolio.py             # 포트폴리오 UI/server → portfolio_structure.md
│       ├── accounts.py              # 계좌 UI/server 진입점 → accounts_structure.md
│       ├── accounts_DAL.py          # 계좌 DB 조회
│       │                            # fetch_accounts_summary() / fetch_account_details()
│       ├── accounts_components.py   # 계좌 카드/행 렌더링
│       │                            # render_asset_card() / render_ticker_row()
│       ├── accounts_modals.py       # 계좌 모달 UI (추가/수정)
│       ├── history.py               # 실적 히스토리 UI/server → history_structure.md
│       ├── history_DAL.py           # 히스토리 DB 조회 + TWR 재계산
│       │                            # load_history() / save_cash_flow() / calc_twr_pct() / calc_ndx_pct()
│       ├── history_charts.py        # Plotly 차트 생성
│       │                            # make_chart_asset() / make_chart_twr()
│       ├── history_table.py         # 일간 누적 테이블 렌더링
│       │                            # render_history_table()
│       ├── history_utils.py         # 히스토리 전용 포맷 유틸
│       │                            # fmt_krw() / fmt_10m()
│       └── settings.py              # 설정 화면 UI/server → settings_structure.md
└── scheduler/
    ├── price_updater.py         # 런처 — interval=0이면 WS모드, >0이면 REST모드 분기
    │                            # → 상세: price_updater_structure.md
    ├── price_updater_common.py  # 공통 모듈 — 설정/DB/시장상태/공휴일/Yahoo/DB업데이트
    │                            # get_market_status() ★ — daily_inserter, settings에서 import
    │                            # get_yahoo_price() / update_ticker_in_db()
    │                            # HolidayCache / get_access_token()
    ├── price_updater_rest.py    # REST 폴링 모드 — N분 주기 전 종목 조회
    │                            # get_kr_price() / get_us_price() / get_confirmed_close_kr()
    │                            # run_update_cycle() / update_worker() / close_confirm_worker()
    ├── price_updater_ws.py      # 웹소켓 모드 — KIS WS 실시간 수신 + Yahoo 폴링
    │                            # get_approval_key() / kis_ws_task() / yahoo_poll_task()
    │                            # subscription_refresh_task() / get_subscribe_targets()
    ├── daily_inserter.py        # 일간 누적 데이터 자동 삽입 → scheduler_structure.md
    │                            # threading.Timer 기반, 매일 daily_insert_time KST 실행
    │                            # 서비스 시작 시 누락 날짜 자동 보정 (_backfill)
    ├── gen_daily_data.py        # 누락 기간 수동 보정용 스탠드얼론 스크립트 (서비스 아님)
    ├── config.json              # 공통 설정값
    │                            # kis_app_key / kis_app_secret / db_password / interval
    │                            # data_go_kr_key / finnhub_api_key
    │                            # retirement_date / daily_insert_time
    │                            # market_map: 마켓별 currency / label / market_time 정의
    │                            #   currency: KRW / USD / NUM(지수)
    │                            #   label: 화면 표시용 마켓명
    │                            #   market_time: KR / US / 24h (시장 운영시간 그룹)
    ├── price_updater.service    # systemd 서비스 파일 원본
    ├── daily_inserter.service   # systemd 서비스 파일 원본
    └── myassets.service         # systemd 서비스 파일 원본
```
---

## 3. 전환 범위 (1차)

### 구현 대상
- 시세 수집 스케줄러 (KIS API / Yahoo Finance)
- DB (종목마스터 / 포지션 / 시세 / 일일누적)
- 웹 대시보드 (지표 표시 + 사용자 입력)
- insert_daily_row 스케줄러 자동화 (매일 오전 7~8시 중 고정 시각)
- 기존 일일자산누적 데이터 (2025-06-19~) DB 일괄 이전
- 텔레그램 봇 (우선순위 최하위)

### 제외 대상 (1차)
- 매수/매도 이력, 평단 관리
- 과거 데이터 일괄 조회 기능

---

## 4. 주요 지표 계산 로직

### IRR (연평균)
- XIRR 기반
- 오늘 총자산을 시작점으로, 과거 입출금 이력과 합쳐서 날짜순 정렬 후 계산
- 원천 데이터: daily_summary의 date / total_asset / cash_flow
- 월평균 IRR은 연평균에서 월할 환산
- 파이썬 구현: numpy_financial 또는 scipy의 xirr 사용 예정

### 알파
- 내 포트폴리오 수익률 - 벤치마크(나스닥100) 수익률
- 기준 시점(고정행) 대비 현재 비율 차이
- 최근 30일 알파도 동일 방식, 기준 시점만 다름
- NDX100 정규화 계수(최초 자산 ÷ 최초 NDX100)는 상수로 별도 관리

### Exposure
- 보유 종목별 (평가액 × 레버리지 배수) 합산 / 총자산
- 레버리지 배수: x1=1, x2=2, x3=3
- FX/INDEX 종목은 positions에 없으므로 자동 제외

### TWR (시간가중수익률)
- 입출금 영향을 제거한 순수 운용 수익률
- twr_asset(오늘) = twr_asset(어제) × (total_asset(오늘) - cash_flow(오늘)) / total_asset(어제)
---

## 5. 화면 구성 (확정)

| 화면 | 역할 |
|------|------|
| 대시보드 | 총자산, 총자산증감, 금일수익, Exposure, 현금/투자비중, x1/x2/x3 비중 바, 월평균 IRR, 월평균 α, 최근 30일 α, 은퇴시점 |
| 포트폴리오 | 전체 종목 통합 뷰 (계좌 구분 없음) — 수량, 평가액, 노출도 등 |
| 계좌 목록/상세 | 계좌 추가/삭제, 계좌별 종목/현금 추가/수정/삭제 |
| 실적 히스토리 | 상단: 추이 그래프, 하단: 일간 누적 데이터 최신순 테이블 |
| 설정 | 앱/화면 설정, 스케줄러 제어 |

---

## 6. DB 스키마 (확정)

### 전제 사항
- **현재가(시세)는 tickers 테이블에 저장.** 별도 JSON 파일 없음.
- **시세 이력 저장 안 함.** 현재가(마지막 시세)만 유지.
- **총자산은 원화 단일값.** 포지션 평가 시 환율 반영 완료된 값으로 계산.
- **입출금은 하루 합산 1건**으로 daily_summary에 포함. 별도 이력 테이블 없음.
- **현금은 positions에 행으로 관리.** ticker 컬럼에 "KRW"/"USD" 고정값 사용.
- **환율(USDKRW=X, JPYKRW=X 등)은 tickers에 market="FX"로 저장.**
- **계좌 삭제 시 해당 계좌의 positions 행은 FK + CASCADE로 자동 삭제.**
> **비율 컬럼 주의**: `exposure`, `cash_ratio`, `x1_ratio`, `x2_ratio`, `x3_ratio`는 소수(0~1 범위)로 저장. 화면 표시 시 `× 100` 후 `%` 붙여서 표시.

### tickers

| 컬럼 | 타입 | 설명 |
|------|------|------|
| ticker | TEXT PK | 종목 티커 |
| name | TEXT | 종목명 |
| market | TEXT | 구분 (KR, NAS, NYS, AMS, ARC, FX, INDEX, CRYPTO, COM) |
| leverage | INT | 레버리지 배수 (1, 2, 3) |
| current_price | NUMERIC | 현재가 (환율 포함) |
| change_pct | NUMERIC | 등락률 |
| updated_at | TIMESTAMP | 마지막 업데이트 시각 (스케줄러 기준) |
| is_manual | BOOLEAN | 수동 추가 항목 여부 (환율/지수 등, 계좌 연동 삭제 대상 제외) |
| data_time | TIMESTAMP | 실제 데이터 시각 (Yahoo Finance만 해당) |

### accounts

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| name | TEXT | 계좌이름 |
| alias | TEXT | 계좌별명 |
| is_watch | BOOLEAN | 감시 계좌 여부 (내 자산 아님, 총자산 합계 제외) |

### positions

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| account_id | INT FK → accounts.id | CASCADE DELETE |
| ticker | TEXT | 종목 티커 또는 "KRW"/"USD" (현금) |
| quantity | NUMERIC | 수량 (현금인 경우 금액) |

### daily_summary

| 컬럼 | 타입 | 설명 |
|------|------|------|
| date | DATE PK | |
| total_asset | NUMERIC | 총자산 (원화) |
| cash_flow | BIGINT | 입출금액 합산 (+ 입금 / - 출금) |
| cash_flow_note | TEXT | 입출금 사유 |
| ndx100 | NUMERIC | NDX100 지수 절대값 |
| exposure | NUMERIC | Exposure 비중 |
| cash_ratio | NUMERIC | 현금비중 |
| x1_ratio | NUMERIC | x1 비중 |
| x2_ratio | NUMERIC | x2 비중 |
| x3_ratio | NUMERIC | x3 비중 |
| twr_asset | NUMERIC | TWR 계산용 보조값 |
| usd_krw | NUMERIC(10,2) | 해당일 기준 USD/KRW 환율 |

---

## 7. DB 구성

- DB명: assetdb / DB 유저: jake / 인증: md5
- VM OS 유저: ubuntu (DB 유저와 불일치 → md5 인증 사용)
- 테이블: tickers, accounts, positions, daily_summary — 생성 완료

### 공통 조회 함수 (db.py)
| 함수 | 반환 | 설명 |
|------|------|------|
| `get_usd_krw()` | `(float, float)` or `(None, None)` | USDKRW=X의 current_price, change_pct 반환. 환율 표시가 필요한 모든 화면에서 사용 |
| `save_config(data)` | `None` | 설정값을 config.json에 저장 |
| `get_db()` | | 컨텍스트 매니저, DB 커넥션 안전 반환. 모든 DAL에서 get_connection() 대신 사용 |
| `get_market_map()` | `dict` | config.json market_map 전체 반환 |
| `get_market_currency(market)` | `str` | 마켓 코드 → 통화 코드 (미정의 마켓은 "KRW" 기본값) |
| `get_market_label(market)` | `str` | 마켓 코드 → 표시 레이블 (미정의 마켓은 코드 그대로) |
| `is_us_market(market)` | `bool` | USD 통화 마켓 여부 |
| `get_supported_markets()` | `list[str]` | market_map에 정의된 전체 마켓 코드 목록 |
---

## 8. 시세 수집 스케줄러

### 파일 구성
| 파일 | 역할 |
|------|------|
| `scheduler/price_updater.py` | 런처 — interval 값으로 REST/WS 모드 분기 |
| `scheduler/price_updater_common.py` | 공통 모듈 (설정, DB, 시장상태, 공휴일, Yahoo) |
| `scheduler/price_updater_rest.py` | REST 폴링 모드 (interval > 0) |
| `scheduler/price_updater_ws.py` | 웹소켓 실시간 모드 (interval = 0) |
| `scheduler/config.json` | 설정값 |
| `scheduler/price_updater.service` | systemd 서비스 파일 원본 |

### 동작 방식
- `interval = 0`: 웹소켓 모드 — KIS WS로 KR/US 종목 실시간 push 수신, FX/INDEX/CRYPTO는 Yahoo 60초 폴링
- `interval > 0`: REST 폴링 모드 — N분 주기로 전 종목 REST API 조회
- 설정 변경 시 `systemctl restart price_updater` → 런처가 새 interval로 모드 재분기
- 시장별 개장 시간 기반 필터링 (get_market_status) — 불필요한 API 호출 차단
- FX / CRYPTO / INDEX / COM: 24시간 조회
- 국내/미국 주식: 현지 장 시간 기반 (pre/open/after/closing/closed)
- 공휴일 캐싱: HolidayCache 클래스, 매일 08:00 KST 1회 갱신
  - 한국: 공공데이터포털 특일 API (`data_go_kr_key`)
  - 미국: Finnhub market-holiday API (`finnhub_api_key`)
- 업데이트 완료 후 PostgreSQL `NOTIFY price_updated` 전송
- systemd 서비스: VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작
- 웹소켓 모드에서 구독 대상 변경 감지 시 os.execv()로 자체 재시작

---

## 9. Shiny 앱 구조

### app.py 구조
- Shiny App + Starlette로 감싸서 실행
- 하단 탭바 JS(`switchTab`)로 탭 전환 (CSS show/hide 방식)
- 모달 열림/닫힘 시 MutationObserver로 body 스크롤 고정 (아이폰 사파리 viewport 틀어짐 방지)
- 각 모듈 ui/server를 네임스페이스로 등록

### 실시간 시세 갱신 구조
- `price_signal.py`: asyncpg로 PostgreSQL `LISTEN price_updated` 대기
- NOTIFY 수신 시 `async with reactive.lock()` → `price_signal.set()` → `await reactive.flush()` 호출
- 각 화면 모듈에서 `price_signal.get()`을 렌더러 안에서 호출해 의존성 등록
- 시세 업데이트 시 의존 렌더러 자동 재실행 → DB 재조회 → 화면 갱신
- 백그라운드 태스크는 첫 세션 접속 시 1회만 시작 (`_task_started` 플래그)

### 설정 화면 구현 특이점

- 시세조회 간격 버튼: 실시간(0) / 1분 / 3분 / 5분 / 10분 / 30분
  - interval=0 선택 시 웹소켓 모드로 전환 (price_updater 서비스 재시작)
  - JS로 active 클래스 전환 + `settings-btn_save_interval` input 세팅 (네임스페이스 하드코딩)
- 티커 목록 내 시장 상태 배지 5종 (open/pre/after/closing/closed), `reactive.invalidate_later(60)` 1분 자동 갱신
- 로그아웃: Shiny server 거치지 않고 JS에서 직접 쿠키 삭제 후 reload
- 티커 추가 시 ON CONFLICT로 기존 티커 덮어쓰기 가능

### 구현 현황
- 하단 탭바 네비게이션 (5개 탭)
- 계좌 목록 화면: 카드형, 계좌 추가/삭제 (삭제 시 JS confirm)
- 계좌 추가 모달에 "감시 계좌" 체크박스 추가 (`is_watch` 컬럼)
- 계좌 목록에서 일반 계좌 / 감시 계좌 섹션 분리 표시
- 총자산 합계 계산 시 감시 계좌 제외
- 계좌 상세 화면: 종목/현금 목록, 종목 추가/수정/삭제, 현금 추가/수정/삭제 (삭제 시 JS confirm)
- 평가액 계산 시 USD 종목 환율(USDKRW=X) 변환 적용
- 종목 추가 시 tickers 미존재 종목은 자동 등록 (market/leverage 반영, is_manual=false)
- 종목 클릭 시 수정 모달 (종목명/시장/레버리지/수량), 현금 클릭 시 수정 모달 (통화/금액) 분기
- 실시간 시세 갱신 (PostgreSQL LISTEN/NOTIFY 기반)
- 모바일 사파리 WebSocket 끊김 대응:
  - iOS Safari는 백그라운드 전환 시 약 5초 후 WebSocket을 능동적으로 끊음 (iOS 레벨 동작, 서버 설정으로 변경 불가)
  - shiny:disconnected 이벤트 감지 시 location.reload()로 자동 재연결
  - 재연결 후 localStorage로 마지막 탭 복원
- plotly 차트는 shinywidgets 대신 fig.to_html(full_html=False, include_plotlyjs=False) + @render.ui 방식 사용. plotly.js는 app.py head에서 CDN으로 전역 로드.
---

## 11. 앞으로 할 일

| 상태 | 항목 |
|------|------|
| ✅ 완료 | 개발범위 설정 |
| ✅ 완료 | 개발스택 결정 |
| ✅ 완료 | 개발환경 구성 (회사 윈도우, 집 맥미니) |
| ✅ 완료 | AI 컨텍스트 md 파일 서빙 API 구축 |
| ✅ 완료 | PostgreSQL 설치 (VM) |
| ✅ 완료 | 화면 구성 확정 |
| ✅ 완료 | DB 스키마 설계 및 생성 |
| ✅ 완료 | 시세 수집 스케줄러 개발 |
| ✅ 완료 | 시세 수집 효율화 (is_market_open 필터링 및 버퍼 적용) |
| ✅ 완료 | 설정 화면 — 티커 목록 내 실시간 수집 상태 배지 표시 |
| ✅ 완료 | Shiny 앱 기본 구조 세팅 (라우팅, DB 연결, 공통 레이아웃) |
| ✅ 완료 | 계좌 목록/상세 화면 (계좌/종목/현금 추가·수정·삭제) |
| ✅ 완료 | 계좌 화면 UI 개선 (일간손익 환율반영, 삼각형 표시, 총자산 요약 섹션, 타이틀바 개선, 삭제버튼 하단 분리) |
| ✅ 완료 | nginx WebSocket timeout 설정 (proxy_read_timeout 3600) |
| ✅ 완료 | 실시간 시세 갱신 (PostgreSQL LISTEN/NOTIFY) |
| ✅ 완료 | nginx Basic Auth 접근 제한 | → Shiny 앱 내 로그인으로 방향 변경 |
| ✅ 완료 | Shiny 앱 로그인 화면 구현 |
| ✅ 완료 | 계좌 화면 환율 표시 (USD/KRW, 등락률, 색상) |
| ✅ 완료 | 설정 화면 구현 (시세조회 간격, 수동 티커 관리, 로그아웃) | → 티커 정렬, 레버리지 뱃지, 수동/자동 구분 표시 포함 |
| ✅ 완료 | 설정 화면 — 스케줄러 interval 조절 |
| ✅ 완료 | 설정 화면 — 티커 정렬 방식 (수동/자동 → 마켓순(KR→US→CRYPTO→COM→FX/INDEX) → 레버리지 높은순 → 알파벳순) |
| ✅ 완료 | 포트폴리오 화면 (전체 종목 통합 뷰) |
| ✅ 완료 | 기존 일일자산누적 데이터 DB 일괄 이전 (2025-06-19~) |
| ✅ 완료 | 과거 입출금내역 변경하도록 개선 → 입출금기록 변경시 twr_asset 업데이트됨 |
| ✅ 완료 | 실적 히스토리 화면 (추이 그래프 + 누적 테이블) |
| ✅ 완료 | 시세 수집 공휴일 캐싱 (HolidayCache 클래스, 매일 08:00 KST 갱신, is_market_open() 연동, update_worker() data_time 버그 수정) |
| ✅ 완료 | 시장 상태 4단계 (`get_market_status()` — open/pre/after/closed), 기존 `is_market_open()` 하위호환 유지 |
| ✅ 완료 | 종가 확정 로직 — closing 상태에서 KR 종가 API 병행 호출, 확정 시 당일 조회 중단 (`_close_confirmed` 플래그) |
| ✅ 완료 | 설정 화면 티커 배지 5종류 (open/pre/after/closing/closed, 색상 구분) |
| ✅ 완료 | 포트폴리오/계좌상세 종목 카드 현재가 표시 (거래 화폐단위, 등락률과 동일 색상) |
| ✅ 완료 | 현금(KRW/USD) 종목 카드 배지 미표시 |
| ✅ 완료 | 시세 수집 웹소켓 모드 추가 (price_updater_ws.py) — KIS WS 실시간 push, Yahoo 폴링 병행 |
| ✅ 완료 | price_updater 3파일 분리 (common/rest/ws) + 런처(price_updater.py)로 모드 분기 |
| ✅ 완료 | 설정 화면 interval 버튼에 실시간(0) 옵션 추가 |
| ✅ 완료 | insert_daily_row 스케줄러 자동화 (daily_inserter.py, systemd 서비스) |
| ✅ 완료 | daily_summary usd_krw 컬럼 추가 (NUMERIC(10,2)) 및 과거 데이터 업데이트 (2025-06-19~2026-05-29) |
| ✅ 완료 | 미국주식 Yahoo로 대체 (daily_snapshot.py) |
| ✅ 완료 | daily_inserter.py threading.Timer 구조로 개편 + 누락 날짜 자동추가 로직 추가 |
| ✅ 완료 | 대시보드 화면 (Bloomberg 스타일 전면 재작성 — SVG 라인차트/도넛, Exposure 통합카드, JetBrains Mono 폰트, dashboard.css 분리) |
| ✅ 완료 | 계좌 목록 화면 감시 계좌 기능 추가 (is_watch 컬럼, 섹션 분리, 총자산 합계 제외) |
| ✅ 완료 | market_map 리팩토링 — 하드코딩 마켓 목록 제거, config.json market_map 중앙화 (currency/label/market_time 필드 추가, 전 파일 적용) |
| ⬜ 대기 | 텔레그램 봇 (우선순위 최하위) |