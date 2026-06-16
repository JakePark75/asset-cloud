# asset-cloud — 이벤트 & 데이터 흐름 맵

> 발행자 / 채널 / 구독자 / 반응 동작을 전체 정리한 문서.  
> 마지막 갱신: 2026-06-15

---

## 1. 전체 구조 개요

이벤트/트리거는 두 계층으로 나뉜다.

| 계층 | 수단 | 범위 | 예시 |
|------|------|------|------|
| **A. 프로세스 간** | Redis pub/sub | 서비스 경계 넘음 | price_updater → myassets |
| **B. 모듈 내부** | Shiny reactive.value | 단일 모듈 안 | 사용자 CRUD → 해당 화면 갱신 |

---

## 2. [계층 A] Redis pub/sub

### 2-1. 채널 구조

```
발행자(Publisher)                  채널                    구독자
────────────────────────────────────────────────────────────────────
price_updater_rest.py    ──┐
price_updater_ws.py      ──┤──► price_updated      ──► price_signal.py
price_updater_common.py  ──┘

accounts.py              ──────► position_changed   ──► price_signal.py

settings.py              ──────► ticker_changed     ──► price_signal.py

daily_inserter.py        ──────► daily_inserted     ──► price_signal.py
```

### 2-2. `price_updated` 채널 — 발행 상세

| 발행자 | 발행 시점 | 비고 |
|--------|-----------|------|
| `scheduler/price_updater_rest.py` | REST 폴링 1사이클 완료 후 | interval > 0 모드 |
| `scheduler/price_updater_ws.py` | KIS WS 메시지 수신 후 주기마다 | interval = 0 모드 |
| `scheduler/price_updater_common.py` | KR 종가 확정 시 | closing 상태 전용 |

### 2-3. `position_changed` 채널 — 발행 상세

| 발행자 | 발행 시점 | 비고 |
|--------|-----------|------|
| `app/modules/accounts.py` | 종목/현금 추가·수정·삭제 완료 후 | recalc_today_row() 포함 |

### 2-4. `ticker_changed` 채널 — 발행 상세

| 발행자 | 발행 시점 | 비고 |
|--------|-----------|------|
| `app/modules/settings.py` | 티커 추가·삭제 완료 후 | tickers 테이블 변경 즉시 반영 |

### 2-5. `daily_inserted` 채널 — 발행 상세

| 발행자 | 발행 시점 | 비고 |
|--------|-----------|------|
| `scheduler/daily_inserter.py` | `daily_summary` INSERT 완료 후 | 매일 `daily_insert_time` KST 1회 |

### 2-6. 구독자 — `price_signal.py` 내부 처리

```
Redis asyncio pubsub
  ├── "price_updated"    수신 → _price_counter += 1
  │                           → price_signal.set(_price_counter)
  │                           → reactive.flush()
  ├── "position_changed" 수신 → _position_counter += 1
  │                           → position_signal.set(_position_counter)
  │                           → reactive.flush()
  ├── "ticker_changed"   수신 → _ticker_counter += 1
  │                           → ticker_signal.set(_ticker_counter)
  │                           → reactive.flush()
  └── "daily_inserted"   수신 → _insert_counter += 1
                              → daily_insert_signal.set(_insert_counter)
                              → reactive.flush()
```

- 백그라운드 태스크: 첫 세션 접속 시 1회만 시작 (`_task_started` 플래그)
- 잠금: `async with reactive.lock()` 보호 하에 signal.set() 호출

### 2-7. 화면별 reactive 반응 매핑

| 화면 | `price_signal` | `position_signal` | `ticker_signal` | `daily_insert_signal` | 반응 동작 |
|------|:--------------:|:-----------------:|:---------------:|:---------------------:|-----------|
| `dashboard.py` | ✅ | ✅ | ✅ | ✅ | 총자산·수익률·비중 등 전체 지표 재계산 |
| `portfolio.py` | ✅ | ✅ | ✅ | ✅ | Redis 시세 재조회 → 종목별 평가액·등락률 갱신 |
| `accounts.py` | ✅ | ✅ | ✗ | ✅ | Redis 시세 재조회 → 계좌별 평가액·손익 갱신 |
| `history.py` (today_row) | ✅ | ✅ | ✗ | ✅ | `recalc_today_row()` → Redis today_row 갱신 → 행 갱신 |
| `history.py` (전체 테이블) | ✅ | ✅ | ✗ | ✅ | DB에 새 행 추가됐으므로 전체 테이블 재전송 |
| `settings.py` | ✅ | ✗ | ✅ | ✗ | Redis 시세 재조회 → 티커 목록 현재가 갱신 |

### 2-8. DB 조회 트리거 매핑

각 화면의 DB 조회는 데이터가 실제로 바뀌는 signal에만 연결됨. `price_updated`는 Redis 시세 조회만 유발.

| 데이터 | DB 재조회 트리거 | 해당 화면 |
|--------|-----------------|-----------|
| `daily_summary` 전체 이력 | `daily_insert_signal` | `dashboard.py` |
| `positions` + `tickers` 메타 (수량·레버리지·마켓) | `position_signal` + `ticker_signal` | `dashboard.py`, `portfolio.py` |
| `tickers` 목록 | `ticker_signal` + `refresh` | `settings.py` |

### 2-9. 발행 시나리오별 흐름

#### 시나리오 1. 시세 업데이트 (REST/WS)
```
price_updater (REST/WS)
  → Redis 시세 키 갱신
  → publish_price_updated()
  → price_signal.py 수신 → price_signal.set()
  → dashboard / portfolio / accounts / history(today_row) / settings
    (DB 재조회 없음 — 각 화면 내부 DB 캐시 그대로, Redis 시세만 재조회)
```

#### 시나리오 2. KR 종가 확정
```
price_updater_common.py (closing 상태 감지)
  → KR 종가 API 호출 → Redis 갱신
  → publish_price_updated()
  → 시나리오 1과 동일한 화면 반응
```

#### 시나리오 3. 계좌에서 종목/현금 추가·수정·삭제
```
사용자 액션 (accounts.py)
  → DB write (positions)
  → recalc_today_row()          ← Redis today_row 즉시 갱신
  → refresh.set(refresh() + 1)  ← accounts 화면 내부 즉시 갱신 (계층 B)
  → publish_position_changed()  ← 타 화면 갱신 (계층 A)
      → position_signal.set()
      → dashboard / portfolio / history(today_row) 재실행
        (positions DB 캐시 무효화 → DB 재조회)
```

#### 시나리오 4. 설정에서 티커 추가·삭제
```
사용자 액션 (settings.py)
  → DB write (tickers)
  → refresh.set(refresh() + 1)  ← settings 화면 내부 즉시 갱신 (계층 B)
  → publish_ticker_changed()    ← 타 화면 갱신 (계층 A)
      → ticker_signal.set()
      → dashboard / portfolio / accounts 재실행
        (tickers DB 캐시 무효화 → DB 재조회)
```

#### 시나리오 5. daily_inserter 자동 실행
```
daily_inserter.py (매일 daily_insert_time KST)
  → daily_summary INSERT
  → publish_daily_inserted()
  → price_signal.py 수신 → daily_insert_signal.set()
  → dashboard / accounts / history(today_row + 전체 테이블) 재실행
    (daily_summary DB 캐시 무효화 → DB 재조회)
```

---

## 3. [계층 B] 모듈 내부 reactive 트리거

프로세스 경계를 넘지 않고, 단일 모듈 안에서 특정 렌더러만 재실행시키는 트리거.

### 3-1. 트리거 목록

| 트리거 | 모듈 | 발생 시점 | 반응 대상 렌더러 |
|--------|------|-----------|-----------------|
| `refresh` | `accounts.py` | 종목/현금 CRUD 완료 후 | 계좌 목록·상세 전체 재렌더 |
| `selected_account` | `accounts.py` | 계좌 카드 클릭 / 삭제 후 초기화 | 계좌 상세 뷰 전환 |
| `refresh` | `settings.py` | 티커 CRUD 완료 후 | 티커 목록 재렌더 (DB 캐시도 무효화) |
| `today_cf_trigger` | `history.py` | 오늘 입출금 저장 후 | today_row 렌더러 강제 갱신 |
| `_reload_trigger` | `history.py` | 과거 입출금 수정 후 | DB rows 전체 재로드 |
| `invalidate_later(60)` | `settings.py` | 60초 주기 자동 | 시장 상태 배지 갱신 |

### 3-2. 내부 트리거 흐름

#### accounts.py
```
사용자 CRUD (계좌/종목/현금 추가·수정·삭제)
  → DB write
  → refresh.set(refresh() + 1)         ← 계좌 화면 즉시 갱신
  → [동시에] publish_position_changed() ← 타 화면 갱신 (계층 A로 상승)

계좌 카드 클릭
  → selected_account.set(acc_id)       ← 상세 뷰 전환

계좌 삭제
  → selected_account.set(None)         ← 상세 뷰 초기화
  → refresh.set(refresh() + 1)
```

#### history.py
```
오늘 입출금 저장
  → DB write
  → today_cf_trigger.set(+1)           ← today_row만 강제 갱신

과거 입출금 수정
  → DB write
  → _reload_trigger.set(+1)            ← DB rows 전체 재로드
```

#### settings.py
```
티커 CRUD
  → DB write
  → refresh.set(refresh() + 1)         ← 티커 목록 즉시 갱신 + DB 캐시 무효화
  → [동시에] publish_ticker_changed()  ← 타 화면 갱신 (계층 A로 상승)

60초 경과
  → invalidate_later(60) 자동 실행     ← 시장 상태 배지만 갱신
```

---

## 4. `initialized` 플래그 — 초기 렌더 가드

트리거가 아니라 **"첫 렌더가 완료됐는가"** 를 기억하는 가드.  
비활성 탭에서 불필요한 재렌더를 막기 위해 사용.

| 플래그 | 모듈 | 역할 |
|--------|------|------|
| `initialized` | `dashboard.py` | 초기 렌더 전 active_tab 가드 스킵 |
| `initialized` | `portfolio.py` | 초기 렌더 전 active_tab 가드 스킵 |
| `initialized` | `accounts.py` | 초기 렌더 전 active_tab 가드 스킵 |
| `initialized` | `settings.py` | 초기 렌더 전 active_tab 가드 스킵 |
| `initialized_today_row` | `history.py` | today_row 첫 렌더 완료 여부 |
| `initialized_historytable` | `history.py` | 전체 테이블 첫 렌더 완료 여부 |

패턴:
```python
# 초기 렌더 전엔 가드 무시 → 렌더 실행 → initialized.set(True)
# 이후엔 비활성 탭이면 조기 return
if initialized.get() and active_tab and active_tab.get() != "xxx":
    return
```

---

## 5. 서비스 간 의존성 (systemd 레벨)

```
[price_updater 서비스]       [myassets 서비스]
  price_updater_*.py    →Redis pub/sub→  price_signal.py
                               │
                           Redis 공유
                               │
                    [daily_inserter]
                    (myassets 프로세스 내
                     threading.Timer로 동작)
```

- `price_updater`와 `myassets`는 독립 프로세스 — Redis만 공유
- `daily_inserter`는 `myassets` 내부에서 실행 (별도 프로세스 아님)
- 세 서비스 모두 VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작

---

## 6. 주요 함수 참조

| 함수 | 위치 | 역할 |
|------|------|------|
| `publish_price_updated()` | `common/redis_store.py` | `price_updated` 채널에 "1" 발행 |
| `publish_position_changed()` | `common/redis_store.py` | `position_changed` 채널에 "1" 발행 |
| `publish_ticker_changed()` | `common/redis_store.py` | `ticker_changed` 채널에 "1" 발행 |
| `publish_daily_inserted()` | `common/redis_store.py` | `daily_inserted` 채널에 "1" 발행 |
| `recalc_today_row()` | `common/redis_store.py` | Redis today_row 키 재계산·갱신 |
| `start_signal_listener()` | `app/price_signal.py` | asyncio pubsub 백그라운드 태스크 시작 |
| `price_signal.get()` | `app/price_signal.py` | Shiny reactive 의존성 등록용 |
| `position_signal.get()` | `app/price_signal.py` | Shiny reactive 의존성 등록용 |
| `ticker_signal.get()` | `app/price_signal.py` | Shiny reactive 의존성 등록용 |
| `daily_insert_signal.get()` | `app/price_signal.py` | Shiny reactive 의존성 등록용 |

---

## 7. 현재 알려진 이슈 (이벤트 흐름 관련)

| 이슈 | 설명 | 상태 |
|------|------|------|
| 모든 탭 reactive 항상 실행 | CSS show/hide 탭 전환이 Shiny의 output suspend 메커니즘을 우회 → 숨겨진 탭의 reactive도 매초 실행됨 | 수정완료 (각 화면 가드 적용) |