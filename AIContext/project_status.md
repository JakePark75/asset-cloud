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

### Shiny 1.6.2 주의사항
- `App()`의 `lifespan` 파라미터 미지원
- 백그라운드 태스크 시작은 `asyncio.get_event_loop().create_task()`를 server 함수 진입부에서 호출하는 방식 사용

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
```
/home/ubuntu/asset-cloud/
├── AIContext/           # AI 컨텍스트 MD 파일 (nginx 정적 서빙)
├── README.md
├── app/
│   ├── app.py           # 진입점 (Shiny App + Starlette 라우팅, 하단 탭바)
│   ├── db.py            # DB 연결 공통
│   ├── context_api.py   # AI 컨텍스트 MD 서빙 API
│   ├── price_signal.py  # 실시간 시세 갱신 신호 (LISTEN/NOTIFY)
│   ├── static/
│   │   └── style.css    # 공통 스타일 (다크테마)
│   └── modules/
│       ├── dashboard.py
│       ├── portfolio.py
│       ├── accounts.py
│       ├── history.py
│       └── settings.py
└── scheduler/
    ├── price_updater.py      # 시세 수집 스케줄러
    ├── config.json           # 설정값 (kis_app_key, kis_app_secret, db_password, interval)
    ├── price_updater.service # systemd 서비스 파일 원본
    └── myassets.service      # systemd 서비스 파일 원본
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
- 현금(KRW/USD)과 IDX 종목은 positions에 없으므로 자동 제외

### TWR (시간가중수익률)
- 입출금 영향을 제거한 순수 운용 수익률

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
- **환율(USDKRW=X, JPYKRW=X 등)은 tickers에 market="IDX"로 저장.**
- **계좌 삭제 시 해당 계좌의 positions 행은 FK + CASCADE로 자동 삭제.**

### tickers

| 컬럼 | 타입 | 설명 |
|------|------|------|
| ticker | TEXT PK | 종목 티커 |
| name | TEXT | 종목명 |
| market | TEXT | 구분 (KR, NAS, AMS, ARC, IDX) |
| leverage | INT | 레버리지 배수 (1, 2, 3) |
| current_price | NUMERIC | 현재가 (환율 포함) |
| change_pct | NUMERIC | 등락률 |
| updated_at | TIMESTAMP | 마지막 업데이트 시각 (스케줄러 기준) |
| is_manual | BOOLEAN | 수동 추가 항목 여부 (환율/지수 등, 계좌 연동 삭제 대상 제외) |
| data_time | TIMESTAMP | 실제 데이터 시각 (IDX/Yahoo Finance만 해당) |

### accounts

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| name | TEXT | 계좌이름 |
| alias | TEXT | 계좌별명 |

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
| total_asset | BIGINT | 총자산 (원화) |
| cash_flow | BIGINT | 입출금액 합산 (+ 입금 / - 출금) |
| cash_flow_note | TEXT | 입출금 사유 |
| ndx100 | NUMERIC | NDX100 지수 절대값 |
| exposure | NUMERIC | Exposure 비중 |
| cash_ratio | NUMERIC | 현금비중 |
| x1_ratio | NUMERIC | x1 비중 |
| x2_ratio | NUMERIC | x2 비중 |
| x3_ratio | NUMERIC | x3 비중 |
| twr_asset | NUMERIC | TWR 계산용 보조값 |

---

## 7. DB 구성

- DB명: assetdb / DB 유저: jake / 인증: md5
- VM OS 유저: ubuntu (DB 유저와 불일치 → md5 인증 사용)
- 테이블: tickers, accounts, positions, daily_summary — 생성 완료

---

## 8. 시세 수집 스케줄러

### 파일 구성
- `scheduler/price_updater.py` — 메인 스크립트
- `scheduler/config.json` — 설정값 (kis_app_key, kis_app_secret, db_password, interval)
- `scheduler/price_updater.service` — systemd 서비스 파일 원본

### 동작 방식
- tickers 테이블 전체 종목 조회 후 market별 API 호출
- 종목별 독립 스레드로 병렬 실행
- KR: KIS API (장마감 시 전일종가 fallback)
- NAS/AMS/ARC: KIS API 미국주식
- IDX: Yahoo Finance (환율/지수/암호화폐 포함, data_time 저장)
- 업데이트 주기: config.json의 interval(분), 실행 중 변경 즉시 반영
- 업데이트 완료 후 PostgreSQL `NOTIFY price_updated` 전송
- systemd 서비스: VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작

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

### 구현 현황
- 하단 탭바 네비게이션 (5개 탭)
- 계좌 목록 화면: 카드형, 계좌 추가/삭제 (삭제 시 JS confirm)
- 계좌 상세 화면: 종목/현금 목록, 종목 추가/수정/삭제, 현금 추가/수정/삭제 (삭제 시 JS confirm)
- 평가액 계산 시 USD 종목 환율(USDKRW=X) 변환 적용
- 종목 추가 시 tickers 미존재 종목은 자동 등록 (market/leverage 반영, is_manual=false)
- 종목 클릭 시 수정 모달 (종목명/시장/레버리지/수량), 현금 클릭 시 수정 모달 (통화/금액) 분기
- 실시간 시세 갱신 (PostgreSQL LISTEN/NOTIFY 기반)
- 모바일 사파리 WebSocket 끊김 대응:
  - iOS Safari는 백그라운드 전환 시 약 5초 후 WebSocket을 능동적으로 끊음 (iOS 레벨 동작, 서버 설정으로 변경 불가)
  - shiny:disconnected 이벤트 감지 시 location.reload()로 자동 재연결
  - 재연결 후 localStorage로 마지막 탭 복원

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
| ✅ 완료 | Shiny 앱 기본 구조 세팅 (라우팅, DB 연결, 공통 레이아웃) |
| ✅ 완료 | 계좌 목록/상세 화면 (계좌/종목/현금 추가·수정·삭제) |
| ✅ 완료 | 계좌 화면 UI 개선 (일간손익 환율반영, 삼각형 표시, 총자산 요약 섹션, 타이틀바 개선, 삭제버튼 하단 분리) |
| ✅ 완료 | nginx WebSocket timeout 설정 (proxy_read_timeout 3600) |
| ✅ 완료 | 실시간 시세 갱신 (PostgreSQL LISTEN/NOTIFY) |
| ✅ 완료 | nginx Basic Auth 접근 제한 | → Shiny 앱 내 로그인으로 방향 변경 |
| ⬜ 대기 | Shiny 앱 로그인 화면 구현 |
| ⬜ 대기 | 포트폴리오 화면 (전체 종목 통합 뷰) |
| ⬜ 대기 | 대시보드 화면 (지표 계산 및 표시) |
| ⬜ 대기 | insert_daily_row 스케줄러 자동화 |
| ⬜ 대기 | 기존 일일자산누적 데이터 DB 일괄 이전 (2025-06-19~) |
| ⬜ 대기 | 실적 히스토리 화면 (추이 그래프 + 누적 테이블) |
| ⬜ 대기 | 설정 화면 (스케줄러 interval 조절, 중지/재시작) |
| ⬜ 대기 | 텔레그램 봇 (우선순위 최하위) |