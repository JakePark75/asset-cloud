# portfolio.py — 구조 요약

## 1. 파일 정보

| 파일 | 역할 |
|------|------|
| `app/app.py` | 최상위 엔트리 포인트, `active_tab` 관리 및 `asset_server()` 상위 호출 |
| `app/modules/portfolio.py` | UI + Server (단일 파일) |
| `app/modules/asset.py` | 히어로 헤더 + 서브탭 관리 (portfolio의 상위 모듈) |
| `app/modules/components.py` | `build_ticker_row_skeleton`, `build_ticker_row_values`, `build_account_row_skeleton`, `build_account_row_values` |
| `app/db.py` | `get_usd_krw()`, `get_config()`, `get_market_currency()` |
| `app/price_signal.py` | `price_signal`, `position_signal`, `ticker_signal` |
| `app/utils/display_diff.py` | `diff_display`, `diff_display_split` |
| `scheduler/price_updater_common.py` | `get_market_status()` |
| `common/redis_store.py` | `get_all_prices()` |

---

## 2. 상위 구조 (app.py → asset.py → portfolio.py)

```
app.py        — 로그인/쿠키 인증, 최상위 탭(자산/실적/관리) 전환, active_tab 관리
 └─ asset.py  — 히어로 헤더(총자산/등락/USD) + 서브탭(대시보드/포트폴리오/계좌) 관리, active_sub_tab 관리
     ├─ dashboard.py
     ├─ portfolio.py   ← 이 문서의 대상
     └─ accounts.py
```

- **공통 헤더는 `asset.py`로 이동됨:** 구버전 `portfolio.py`에 있던 총자산/손익/USD 요약 헤더(`#pf-total-asset` 등)는 제거가 아니라 `asset.py`의 히어로 영역(`#asset-hero-amount`, `#asset-hero-delta-text`, `#asset-hero-usd-text`)으로 이동. `asset_hero_update` 커스텀 메시지로 갱신되며, `portfolio`/`accounts` 서브탭과 무관하게 항상 표시.
- **"어제 대비" 비교 로직도 `asset.py`로 이동됨:** 구버전 `_db_yesterday_total()`(`daily_insert_signal` 구독)은 `asset.py`의 `_db_summary_rows()` + `_hero_data()` 내부 `prev_asset = to_f(summary_rows[-1][1])`로 대체.
- `portfolio_server`는 `asset_server`가 호출하며 `active_tab`, `active_sub_tab` 둘 다 전달받음 — 비활성 탭/서브탭일 때 `_send_update` 스킵 가드에 사용.
- `build_summary_header_dom` / `build_summary_payload`(`components.py`)는 `portfolio.py`에서는 쓰이지 않음. `accounts.py` 확인 전이라 그쪽에서의 사용 여부는 미확인(추론: 계좌 통합 자산 요약에 쓰일 가능성이 높으나 미확정).

---

## 3. 역할
- 전체 계좌 통합 종목 뷰 (`is_watch = false` 계좌 보유 종목)
- **감시종목(watch-only) 섹션** — 감시계좌에만 존재하고 비감시계좌 보유가 없는 ticker, 목록 하단 별도 섹션(`<h4 class="section-heading">감시종목</h4>`)
- 종목 클릭 시 **인라인 아코디언**으로 해당 종목 보유 계좌 목록을 행 바로 아래에 펼침 (전체화면 드릴다운 → 아코디언으로 전환, 한 번에 하나만 열림)
- 종목별 평가액, 현재가, 등락률, 시장 상태 배지, 평단가, 수익률 표시
- 강제 시세 조회 버튼 (`↺`) — `interval != 0`일 때만 표시
- `render.ui` 없음 — 커스텀 메시지로 DOM 직접 패치

---

## 4. 데이터 흐름 (DAL 함수)

### `load_portfolio(db_rows)`
- Redis `get_all_prices()`로 시세 주입
- `db_rows`: `_db_portfolio_rows()` 결과 (6-tuple: ticker, qty, name, market, leverage, avg_price)
- 반환: `[(ticker, qty, name, price, change_pct, market, leverage, avg_price), ...]`
- (구버전과 차이) `yesterday_total` 파라미터/반환값 제거됨

### `load_watch_only(db_rows)` — 신규
- 감시계좌 전용 보유 ticker에 Redis 시세 주입
- `db_rows`: `_db_watch_only_tickers()` 결과 (4-tuple: ticker, name, market, leverage)
- qty/avg_price는 보유 없음을 의미하는 `0`/`None`으로 고정
- 반환: `load_portfolio`와 동일한 8-tuple 형태 리스트

### `load_ticker_accounts(ticker, db_rows, usd_rate)`
- 특정 ticker의 보유 계좌 목록(아코디언 내용)에 Redis 시세 주입
- 가격은 `position_signal` 구독 캐시(`_db_ticker_accounts`) 기반이며 `price_signal`과는 무관
- `db_rows`: `_db_ticker_accounts()` 결과 (9-tuple: acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, ticker_name)
- 반환: `(acc_rows, price, chg_pct)` — acc_rows: 11-tuple `(acc_id, acc_name, alias, is_watch, qty, avg_price, market, leverage, price, chg_pct, ticker_name)`

---

## 5. 핵심 로직 / 헬퍼 함수

| 함수 | 설명 |
|------|------|
| `_ticker_to_id(ticker)` | 하이픈/캐럿 → 언더스코어 (DOM id 안전화) |
| `_calc_amount(ticker, qty_f, price_f, market, usd_rate)` | KRW/USD/미국주식/국내주식 분기 평가액 계산 |
| `_sort_rows(rows, usd_rate)` | 현금(KRW/USD) 하단, 나머지 평가액 내림차순 |
| `_sort_watch_rows(rows)` — 신규 | 감시종목은 평가액이 의미 없어 이름/ticker 기준 고정 정렬 (구성 변경 감지가 매번 흔들리는 것 방지) |
| `_build_pf_row_skeleton(ticker, qty, name, market, leverage, avg_price=None)` | 포트폴리오 종목 행 골격 + **빈 아코디언 컨테이너**(`#pf-acc-{tid}`, 기본 `display:none`) |
| `_build_pf_tick_values(ticker, qty, name, price, chg_pct, market, leverage, usd_rate, avg_price=None, is_watch_only=False)` | 포트폴리오 종목 tick 값 dict (static/dynamic 분리 — 아래 참고) |
| `_build_drilldown_row_skeleton(acc_id, acc_name, alias, qty)` | 아코디언 내부 계좌 행 골격 — 계좌명/수량만 (구버전보다 단순화) |
| `_build_drilldown_row_values(acc_id, ticker, qty, avg_price, price, market, usd_rate)` | 아코디언 내부 계좌 행 tick 값 — 평가금액 + 해당 계좌 포지션 손익액/수익률 |
| `_build_accordion_html(acc_rows)` — 신규 | 아코디언 내부 계좌 목록 HTML 조립 (일반 계좌 + 감시 계좌 섹션 분리, 헤더 없음 — 가격/손익은 종목 행에 이미 표시됨) |

### `_build_pf_row_skeleton()` 구조
- `build_ticker_row_skeleton(...)` 호출 — `is_cash`는 ticker 기반 내부 판단
- 현금(KRW): qty 영역 없음 (`qty_fixed=""`)
- 현금(USD): qty 영역에 통합 표시 (`qty_fixed=None`, 1행 구조)
- 일반 종목: `qty_fixed=None`, `onclick_attr`에 `pfToggleTicker('{ticker}', '{tid}')` 연결
- 현금이 아닌 경우에만 행 뒤에 `<div class="subtab-accordion" id="pf-acc-{tid}" style="display:none;"></div>` 추가

### tick 값 — static / dynamic 분리 (신규)
구버전은 단일 dict였으나, 현재는 `build_ticker_row_values`가 `{"static": {...}, "dynamic": {...}}` 형태로 분리 반환:

| 구분 | 포함 필드(추정 — `_applyOneTickerFull`/`_applyOneTickerStatic`/`_applyOneTicker` JS 기준) |
|------|------|
| static | `name`, `leverage`, `qty`, `avgprice`, `status_dot`/`status_txt`/`status_cls` |
| dynamic | `amount`, `price`, `chg`, `chg_css`, `pnl`, `pnl_css` |

→ 초기 렌더(`pf_init`) 시엔 static+dynamic 합쳐 전송, 이후 변경분만 `pf_tick`(dynamic)/`pf_static_tick`(static)으로 분리 전송 — 고빈도 가격 변동과 저빈도 메타 정보 변경을 분리해 불필요한 DOM 패치 최소화.

### `_build_drilldown_row_skeleton()` / `_build_drilldown_row_values()` 구조 (단순화됨)
- 구버전: `build_ticker_row_skeleton` 재사용, qty 고정값 + avgprice/pnlpct span
- 현재: `build_account_row_skeleton` / `build_account_row_values` 전용 함수로 분리
  - skeleton: 계좌명(+alias) + 수량 텍스트만 고정 표시
  - values: 평단가, 평가금액, **손익액**(`pnl_amount`, 신규 — 구버전엔 없었음), 손익률(%), 통화

---

## 6. UI (`portfolio_ui()`)

### 뷰 구조 (구버전과 큰 차이)
- **별도 드릴다운 풀스크린 뷰 없음** — `#pf-drilldown-view`, `#pf-back-btn` 등 모두 제거
- 단일 컨테이너 `#pf-ticker-list` 안에서 각 종목 행 + 그 바로 아래 아코디언(`#pf-acc-{tid}`)이 함께 렌더링
- 강제조회 버튼(`#pf-force-btn-wrap`)만 별도 wrap으로 존재
- 공통 헤더(`#pf-total-asset`, `#pf-pnl`, `#pf-usd-wrap`) — **현재 코드에 없음**. `asset.py` 히어로 영역으로 이동 확인됨 (위 "2. 상위 구조" 섹션 참고)

### JS 커스텀 메시지 핸들러

| 핸들러 | 동작 |
|--------|------|
| `pf_init` | `#pf-ticker-list` 골격 통째 교체, 강제조회 버튼 표시 제어, 이전에 열려있던 아코디언 id가 사라졌으면 `window._pfOpenTid` 초기화, static+dynamic 전체 적용 |
| `pf_tick` | dynamic 필드 변경분만 패치 (`_applyOneTicker`) |
| `pf_static_tick` — 신규 | static 필드(이름/레버리지/수량/평단/시장상태) 변경분만 패치 (`_applyOneTickerStatic`) |
| `pf_acc_init` | `#pf-acc-{tid}` 내부 HTML 통째 교체 + `display:''`로 펼침, 계좌 행 값 적용 |
| `pf_acc_tick` | 열려있는 아코디언 내 변경된 계좌 행만 패치 |

→ 구버전의 `pfd_init`/`pfd_tick` (별도 드릴다운 뷰 갱신)은 `pf_acc_init`/`pf_acc_tick`으로 대체.

### JS 전역 함수

| 함수 | 설명 |
|--------|------|
| `pfToggleTicker(ticker, tid)` | 아코디언 토글. 이미 열려있으면 닫고(`ticker_clicked: {ticker: null}` 이벤트), 다른 게 열려있으면 그것부터 닫은 뒤 새로 펼침(한 번에 하나만 열림 보장) |

→ 구버전의 `pfOpenTickerDrilldown` / `pfGoBack`은 제거되고 토글 함수 하나로 통합.

### DOM 패치 id 패턴 (변경 없음, 유지)

| id 패턴 | 내용 |
|---------|------|
| `pf-amount-{tid}` | 원화 평가액 |
| `pf-qty-{tid}` | 수량 |
| `pf-price-{tid}` | 현재가 |
| `pf-chg-{tid}` | 등락률 |
| `pf-avgprice-{tid}` | 평단가 |
| `pf-pnl-{tid}` | 손익(텍스트/css) — 구버전 `pf-pnlpct-{tid}`에서 명칭/구조 변경 가능성, 코드상 `pnl`/`pnl_css` 필드명 기준 |
| `pf-status-{tid}` | 시장 상태 배지 |
| `pfd-amount-{acc_id}` | 아코디언 내 계좌 평가액 |
| `pfd-avgprice-{acc_id}` | 아코디언 내 계좌 평단가 |
| `pfd-pnl-{acc_id}` | 아코디언 내 계좌 손익(텍스트+css 동시 적용) |

---

## 7. Server (`portfolio_server()`)

### 시그니처 변경
```python
def portfolio_server(input, output, session,
                      active_tab: reactive.value = None,
                      active_sub_tab: reactive.value = None):
```
→ `active_sub_tab` 신규 추가. `_send_update`에서 `tab = active_sub_tab if active_sub_tab is not None else active_tab` 형태로 서브탭 우선 사용.

### Reactive / 일반 상태

| 변수 | 타입 | 설명 |
|------|------|------|
| `_initialized` | **plain bool** (reactive.value 아님) | 첫 렌더 완료 여부. effect 자기 재트리거 방지 위해 `nonlocal`로 관리 |
| `open_ticker` | `reactive.value(None)` | 구버전 `selected_ticker` 대체. `None`: 전체 닫힘, `str`: 해당 ticker 아코디언 열림 (한 번에 하나만) |
| `_last_tickers` | list | 이전 종목 목록(일반+감시 합산, 구성 변경 감지) |
| `_last_display` | dict | 이전 표시값 (diff 기준, static+dynamic 합쳐 관리되는 것으로 보임) |
| `_last_open_ticker` | str \| None | 이전에 열려있던 ticker (전환 감지용, 신규) |
| `_last_dd_accounts` | list | 이전 아코디언 계좌 목록 |
| `_last_dd_display` | dict | 이전 아코디언 표시값 |

### DB 캐시 (`@reactive.calc`)

| calc | 구독 신호 | 설명 |
|------|-----------|------|
| `_db_portfolio_rows()` | `position_signal`, `ticker_signal` | positions + tickers JOIN, `WHERE a.is_watch = false` |
| `_db_watch_only_tickers()` — 신규 | `position_signal`, `ticker_signal` | 감시계좌 전용 보유(비감시계좌 보유 0) ticker, `NOT IN` 서브쿼리 |
| `_db_ticker_accounts()` | `position_signal`, `ticker_signal`, `open_ticker` | 열려있는 아코디언의 ticker 보유 계좌 목록. `open_ticker`가 `None`이면 쿼리 없이 빈 리스트 즉시 반환 (불필요한 DB 호출 방지) |

→ 구버전의 `_db_yesterday_total()` (`daily_insert_signal` 구독, daily_summary 최신 1행) — **현재 코드에 없음**. `asset.py`의 `_db_summary_rows()` + `_hero_data()`로 이동 확인됨 (위 "2. 상위 구조" 섹션 참고).

### `_send_update` (`@reactive.effect`, async)
- 구독: `price_signal`, `position_signal`, `ticker_signal`
- `open_ticker.get()`을 **탭 가드(active_tab/active_sub_tab 체크) 이전에** 호출 — 의존성 등록을 먼저 보장하기 위한 의도적 순서 (코드 주석상 명시)
- `_initialized`가 True이고 현재 탭이 `"portfolio"`가 아니면 즉시 return (비활성 탭 스킵)
- 처리 순서:
  1. `get_usd_krw()`로 환율 조회 (매 tick, Redis)
  2. 일반 종목(`load_portfolio`) + 감시종목(`load_watch_only`) 각각 로드 → 정렬 → tick 값 dict 병합
  3. 종목 구성 변경 시: `pf_init` 전송 (골격 HTML 통째 + static/dynamic 합친 값)
  4. 구성 동일 시: `diff_display_split`로 static/dynamic 분리 diff → `pf_tick`(dynamic) / `pf_static_tick`(static) 각각 필요한 경우만 전송
  5. **아코디언 처리는 `open_ticker`가 있을 때만 추가 수행** — 없으면 DB 조회/연산 스킵
     - ticker 전환 또는 계좌 구성 변경 시: `pf_acc_init` 전송 (HTML 통째 + 전체 값)
     - 동일 구성 시: `diff_display`로 변경분만 `pf_acc_tick` 전송

### 이벤트 핸들러

| 핸들러 | 트리거 | 동작 |
|--------|--------|------|
| `_handle_ticker_click` | `input.ticker_clicked` | payload에서 `ticker` 추출, 없으면(닫기) `_last_dd_accounts`/`_last_dd_display` 초기화 후 `open_ticker.set(ticker)` |
| `_show_force_modal` | `input.force_update` | 확인/취소 모달 표시 |
| `_do_force_update` | `input.force_confirm` | 모달 제거 + `price_updater.py --force` 서브프로세스 실행 |
| `_cancel_force_update` | `input.force_cancel` | 모달 제거만 |

→ 구버전의 `_handle_go_back`(뒤로가기) — **제거됨**. 아코디언 토글 방식에서는 같은 `ticker_clicked` 핸들러가 열기/닫기를 모두 처리(payload의 `ticker`가 `null`이면 닫기).

---

### components.py 사용 함수 — 구버전과 차이

| 함수 | 구버전 | 현재 |
|------|--------|------|
| `build_ticker_row_skeleton` | 포트폴리오 + 드릴다운 둘 다 사용 | **포트폴리오 행만** 사용 |
| `build_ticker_row_values` | 포트폴리오 + 드릴다운 둘 다 사용, static/dynamic 미분리 | **포트폴리오만**, `{"static": ..., "dynamic": ...}` 분리 반환 |
| `build_account_row_skeleton` / `build_account_row_values` | 없음 | **신규** — 아코디언 내부 계좌 행 전용 (구버전엔 `build_ticker_row_skeleton` 재사용했던 부분을 대체) |
| `build_summary_payload` / `build_summary_header_dom` | (구버전 문서에는 없었음) | `portfolio.py`에서 미사용 확인 → import 제거 완료. `accounts.py`에서 사용 여부는 미확인 |

---

## 8. 변경 이력 / 확인된 사실

- ✅ **데드코드 제거**: `fmt_pct`, `build_summary_payload` 미사용 import 확인 후 제거 완료.
- ⚠️ **미정리 항목(영향 없음)**: `usd_chg`(`get_usd_krw()` 두 번째 반환값)는 받아만 두고 미사용 — lint성 항목, 다음에 이 파일 다시 만질 때 같이 정리 권장.
- ✅ **공통 헤더 이동 확인**: `#pf-total-asset` 등 → `asset.py` 히어로 영역으로 이동 (제거 아님).
- ✅ **"어제 대비" 비교 로직 이동 확인**: `_db_yesterday_total()` → `asset.py`의 `_db_summary_rows()` + `_hero_data()`.