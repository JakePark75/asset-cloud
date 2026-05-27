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
| 도메인 | myassets.mooo.com |
| DB | PostgreSQL (VM에 직접 설치) |
| 프레임워크 | Shiny for Python |
| 리포지토리 | https://github.com/JakePark75/asset-cloud |

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
| 계좌 목록/상세 | 계좌 추가/삭제, 계좌별 종목/현금 추가/삭제, 종목별 평가액/비중 등 |
| 실적 히스토리 | 상단: 추이 그래프, 하단: 일간 누적 데이터 최신순 테이블 |
| 설정 | 앱/화면 설정 |

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
- `scheduler/price_updater.service` — systemd 서비스 파일

### 동작 방식
- tickers 테이블 전체 종목 조회 후 market별 API 호출
- 종목별 독립 스레드로 병렬 실행
- KR: KIS API (장마감 시 전일종가 fallback)
- NAS/AMS/ARC: KIS API 미국주식
- IDX: Yahoo Finance (환율/지수/암호화폐 포함, data_time 저장)
- 업데이트 주기: config.json의 interval(분), 실행 중 변경 즉시 반영
- systemd 서비스: VM 재부팅 시 자동 시작, 크래시 시 10초 후 자동 재시작

---

## 9. 앞으로 할 일

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
| ⬜ 대기 | Shiny 앱 기본 구조 세팅 (라우팅, DB 연결, 공통 레이아웃) |
| ⬜ 대기 | 계좌 목록/상세 화면 (계좌/종목/현금 추가·삭제) |
| ⬜ 대기 | 포트폴리오 화면 (전체 종목 통합 뷰) |
| ⬜ 대기 | 대시보드 화면 (지표 계산 및 표시) |
| ⬜ 대기 | insert_daily_row 스케줄러 자동화 |
| ⬜ 대기 | 기존 일일자산누적 데이터 DB 일괄 이전 (2025-06-19~) |
| ⬜ 대기 | 실적 히스토리 화면 (추이 그래프 + 누적 테이블) |
| ⬜ 대기 | 설정 화면 |
| ⬜ 대기 | 텔레그램 봇 (우선순위 최하위) |