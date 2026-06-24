# 뉴스 피드 기능 구현 — 작업 정리

작업일: 2026-06-24

## 1. DB 테이블 생성 (완료)

```sql
CREATE TABLE news_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    url TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE news_keywords (
    id SERIAL PRIMARY KEY,
    keyword VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

초기 데이터 (RSS 소스 3개) insert 완료:
- CNBC World — `https://www.cnbc.com/id/100003114/device/rss/rss.html`
- CNBC Tech — `https://www.cnbc.com/id/19854910/device/rss/rss.html`
- Yahoo Finance — `https://finance.yahoo.com/news/rssindex`

`news_keywords`는 비어있는 상태로 시작 (UI에서 사용자가 직접 추가).

확인용 명령어:
```bash
PGPASSWORD=qkrworb0! psql -U jake -d assetdb -c "SELECT * FROM news_sources;"
PGPASSWORD=qkrworb0! psql -U jake -d assetdb -c "SELECT * FROM news_keywords;"
```

---

## 2. `common/redis_store.py` 수정 (완료)

기존 파일 끝에 함수 4개 추가 (기존 코드 변경 없음):

| 함수 | 역할 | TTL |
|---|---|---|
| `get_news_translation_cache(url_hash)` | 캐시된 번역 제목 조회 | - |
| `set_news_translation_cache(url_hash, translated_title, ttl=3600)` | 번역 제목 캐시 저장 | 1시간 |
| `get_news_feed_cache()` | 캐시된 뉴스 피드 리스트 조회 | - |
| `set_news_feed_cache(items, ttl=300)` | 매칭된 뉴스 피드 캐시 저장 | 5분 |

**설계 근거**: 기존 코드베이스 전체가 "Redis 접근은 `common/redis_store.py`를 거친다"는 단일 진입점 구조를 따르고 있어, `news_fetcher.py`와 `settings.py` 양쪽에서 동일 함수를 재사용하도록 함.

키 구조:
- `news:translated:{md5(url)}` → 번역된 제목 문자열
- `news:feed` → 매칭된 기사 리스트 JSON

---

## 3. `scheduler/news_fetcher.py` 신규 작성 (완료)

`daily_inserter.py`와 동일한 독립 실행 구조 (`threading.Timer`, while 루프 없음, KST 명시).

**배치 위치**: `/home/ubuntu/asset-cloud/scheduler/news_fetcher.py` (`price_updater.py`와 동일 디렉토리 — `PROJECT_ROOT` 계산 방식이 같아야 하므로)

**동작 흐름**:
1. DB에서 `enabled=true`인 `news_sources` + 전체 `news_keywords` 로드
2. 각 소스를 `feedparser`로 폴링
3. 제목에 키워드가 **단어 단위(word boundary, `\b`)로 매칭**되는 기사만 추출
   - 영어 키워드(예: `AI`)는 `said` 같은 단어 내부 문자열에 우연히 매칭되지 않도록 단어 경계 적용
   - ⚠️ 한글 키워드는 조사가 붙는 한국어 특성상 단어경계 매칭이 구조적으로 거의 항상 실패함을 확인 (`삼성전자가` ≠ `삼성전자\b`) → 키워드는 항상 영어로 저장하는 것으로 설계 확정 (3번 섹션 참고)
4. 매칭된 기사만 제목 번역 (URL 해시 기준 Redis 캐시 우선 조회 → 중복 번역 방지)
   - 번역 실패 시(`TooManyRequests`, `RequestError`, `TranslationNotFound` 등) 원문 제목 + `[번역실패]` 표시, 해당 기사 스킵하지 않고 계속 진행
5. 매칭 기사를 발행시각 내림차순 정렬 후 `news:feed`에 캐시 (TTL 5분)
6. 5분(300초) 주기로 반복

**날짜 처리 (검색 확인된 정석 방식)**:
> feedparser의 `published_parsed`는 UTC 기준 `struct_time`으로 정규화되어 있음. `time.mktime()`을 쓰면 로컬 시간대로 잘못 해석되어 시간이 틀어지므로, `calendar.timegm()`으로 UTC epoch를 구한 뒤 timezone-aware datetime으로 변환해야 함. (기존 UTC/KST 버그 패턴과 동일 종류의 함정)

**의존성**: `feedparser`, `deep-translator` — VM에 설치 확인됨 (`/usr/bin/python3 -c "import feedparser, deep_translator"` → OK)

---

## 4. systemd 서비스 등록 (완료, 동작 확인됨)

`/etc/systemd/system/news_fetcher.service`:
```ini
[Unit]
Description=Asset Cloud News Fetcher
After=network.target postgresql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/asset-cloud/scheduler
ExecStart=/usr/bin/python3 /home/ubuntu/asset-cloud/scheduler/news_fetcher.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

등록/실행:
```bash
sudo systemctl daemon-reload
sudo systemctl enable news_fetcher
sudo systemctl start news_fetcher
journalctl -u news_fetcher -f
```

**동작 확인됨** (2026-06-24 11:23 KST): 정상 기동, 키워드 없을 때 "매칭 기사 없음" 로그까지 확인. 실제 키워드 매칭 테스트는 아직 미완료 (5번 항목 참고).

---

## 5. `app/modules/settings.py` 수정 (완료, VM 배포/테스트 전)

기존 UI 패턴(모달, `Shiny.setInputValue`, 토글 스위치, `st_init`/`st_tick` 커스텀 메시지 핸들러 구조)을 그대로 따라 "티커 관리" 섹션과 "내보내기" 섹션 사이에 3개 영역 추가.

### 5-1. 뉴스 소스 토글
- `news_sources` 테이블의 `enabled` 컬럼을 켜고 끄는 토글 스위치
- 토글 변경 시 즉시 DB UPDATE (`news_fetcher.py`는 5분 주기로 DB를 다시 읽으므로 별도 Redis 신호 발행 불필요)

### 5-2. 키워드 관리
- 입력창 + **[번역]** 버튼 + **[추가]** 버튼 + 기존 키워드 칩 목록(삭제 버튼 포함)
- **[번역] 버튼**: 입력창 내용을 `GoogleTranslator(source='auto', target='en')`으로 번역해 **입력창 값을 그 자리에서 교체** (DB에는 저장 안 됨)
  - 예: `반도체` 입력 → 번역 클릭 → 입력창이 `semiconductor`로 바뀜
- **[추가] 버튼**: 그 시점 입력창 내용(영어로 변환된 상태)을 `news_keywords.keyword`로 INSERT
- **설계 확정 배경**: 한글 키워드를 그대로 저장하면 영어 원문 제목과 매칭이 구조적으로 불가능하므로(번역 전 매칭이라 원문이 영어), 저장 시점에 항상 영어로 변환해 저장하는 방식으로 결정. 한글 원본은 따로 저장하지 않음(요청에 따라 불필요 판단).
- 번역 API 호출은 별도 백엔드 분리 없이 `settings.py` 서버 함수 내에서 동기 호출 (Shiny가 같은 프로세스 내 서버 함수 호출 구조이고, 오라클 프리티어 안에서 별도 엔드포인트를 둘 이유가 없다고 판단)

### 5-3. 뉴스 피드 표시
- `get_news_feed_cache()`를 60초마다 조회해 표시 (탭이 "settings"가 아닐 때는 스킵 — 기존 `_send_update`와 동일한 비활성 탭 스킵 패턴)
- 번역된 제목(또는 번역 실패 시 원문+`[번역실패]`) + 원문 링크 + 소스명 + 발행시각(KST 변환) 표시

### 변경 파일 요약
- import: `deep_translator`, `datetime`, `zoneinfo.ZoneInfo`
- CSS: 범용 토글(`news-toggle-checkbox`), 키워드 칩, 피드 아이템 스타일 추가
- JS: `st_news_sources` / `st_news_keywords` / `st_news_feed` / `st_news_translated` 커스텀 메시지 핸들러 추가
- Server: `_news_source_rows`/`_news_keyword_rows`(`@reactive.calc`), 소스 토글/키워드 추가/삭제/번역/피드표시 `@reactive.effect` 6개 추가

---

## 6. 다음 세션에서 진행할 것 (미완료)

1. **`settings.py` VM 배포 및 실제 테스트**
   - 영어 키워드(예: `semiconductor`, `AI`) 등록 후 `news_fetcher.py` 재시작/대기로 실제 매칭 확인
   - Redis에 `news:feed` 캐시가 정상 저장되는지 확인 (`redis-cli GET news:feed`)
   - 설정 탭 UI에서 토글/키워드 추가삭제/피드 표시가 실제로 정상 렌더링되는지 화면 확인
2. **키워드 [번역] 버튼 동작 확인** — 한글 입력 시 영어로 정상 치환되는지, 실패 시 콘솔 로그(`[settings] 키워드 번역 실패 ...`)만 찍히고 조용히 무시되는 현재 동작이 사용자 입장에서 괜찮은지(피드백 UI 없음) 재검토 필요할 수 있음
3. **뉴스 피드 새로고침 주기(60초) 적절성 재검토** — 캐시 자체는 5분 주기 갱신이므로 60초는 다소 과한 polling일 수 있음, 필요시 120~300초로 조정 가능
4. **CSS/모달 충돌 여부 실제 화면에서 확인** — 코드 리뷰로는 충돌 가능성 낮다고 판단했으나 실제 브라우저 렌더링 확인 필요

---

## 참고: 적용된 핵심 설계 원칙 (이번 세션에서 재확인/적용)

- DB/Redis 접근은 항상 공용 모듈(`redis_store.py`)을 거치는 구조 유지 — 직접 호출 금지
- 키워드 매칭은 항상 영어(원문 RSS 언어) 기준으로 이루어지므로, 사용자가 입력하는 키워드도 저장 시점에 영어로 정규화
- 기술/API 동작 방식(feedparser 날짜 처리, deep-translator 예외 종류)은 기억이 아닌 웹 검색으로 사실 확인 후 코드 작성
- 오라클 클라우드 프리티어 제약 — 별도 백엔드/엔드포인트 추가 없이 기존 프로세스(Shiny 서버 함수) 내에서 번역 호출 처리