# bid-collectors 개발 상세 계획

> **관련 문서:**
> - [bid_collectors.md](bid_collectors.md) — 패키지 설계 + API 목록
> - [dev_reference.md](dev_reference.md) — lets_portal 수집기 코드 레퍼런스
> - [pre_ready.md](pre_ready.md) — 개발 전 준비사항

---

## Phase 0: 프로젝트 셋업 (1~2일)

### 0-1. 프로젝트 초기화

```
bid-collectors/
├── bid_collectors/
│   ├── __init__.py          패키지 버전, 공개 API export
│   ├── base.py              BaseCollector 추상 클래스
│   ├── models.py            Notice (Pydantic v2)
│   └── utils/
│       ├── __init__.py
│       ├── dates.py         날짜 파서 (lets_portal _parse_date + format_date 통합)
│       ├── text.py          HTML 정리 (lets_portal clean_html, clean_html_to_text 이식)
│       ├── http.py          공통 HTTP 클라이언트 (재시도, 타임아웃, User-Agent)
│       └── status.py        공고 상태 판정 (ongoing/closed)
├── tests/
│   ├── conftest.py          공통 fixture (mock API 응답, 샘플 Notice)
│   └── test_utils.py
├── pyproject.toml           패키지 메타데이터, 의존성
├── .env.example             필요한 환경변수 목록
└── README.md
```

### 0-2. 핵심 의존성

```toml
[project]
name = "bid-collectors"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",           # async HTTP (requests 대체)
    "beautifulsoup4>=4.12",  # HTML 파싱
    "pydantic>=2.0",         # 데이터 모델
    "lxml>=5.0",             # XML 파싱 (나라장터 등)
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio", "respx", "ruff"]
```

### 0-3. BaseCollector + Notice 모델 구현

```python
# models.py — Pydantic v2
class Notice(BaseModel):
    source: str
    bid_no: str
    title: str
    organization: str
    start_date: date | None = None
    end_date: date | None = None
    status: str = "ongoing"
    url: str
    detail_url: str = ""
    content: str = ""
    budget: int | None = None
    region: str = ""
    category: str = ""
    attachments: list[dict] | None = None
    extra: dict | None = None

# base.py
class BaseCollector(ABC):
    source_name: str

    @abstractmethod
    async def collect(self, days: int = 1, **kwargs) -> list[Notice]: ...

    async def health_check(self) -> dict: ...
```

**결정 사항:**
- lets_portal은 동기(requests) → bid-collectors는 **비동기(httpx)** 로 전환
- save_to_db 제거 — 수집기는 데이터 fetch만, 저장은 BidWatch 본체 책임
- collect_and_save → collect로 단순화

### 0-4. 유틸리티 이식

lets_portal에서 가져와 통합:
- `utils/dates.py`: `_parse_date()` + `format_date()` → 하나로 통합 (`_parse_date`가 더 강력)
- `utils/text.py`: `clean_html()` + `clean_html_to_text()` → 그대로 이식
- `utils/status.py`: `determine_status()` + `_get_status()` → 하나로 통합
- `utils/http.py`: 공통 httpx 클라이언트 (재시도 3회, 타임아웃 15초, User-Agent)

**테스트:** 날짜 파서 5가지 패턴, HTML 정리, 상태 판정 단위 테스트

---

## Phase 1: MVP 수집기 5개 (1~2주)

### 1-1. 나라장터 (`nara.py`)

**이식 원본:** lets_portal `collectors/nara.py` (dev_reference.md §3)

**변경 사항:**
- 키워드별 호출 → **키워드 없이 당일 전체 공고 수집**
  - `bidNtceNm` 파라미터 제거
  - 날짜 범위만으로 조회 (inqryBgnDt ~ inqryEndDt)
- 동기(requests) → 비동기(httpx)
- XML 파싱: `lxml.etree` 사용
- 관심 중분류 필터 제거 (BidWatch 조회 시점에서 처리)
- 3개 서비스(용역/물품/공사) 순차 또는 병렬 호출
- 429 에러 재시도 로직 유지 (30초 대기, 3회)

**구현 순서:**
1. 용역(getBidPblancListInfoServcPPSSrch) 단일 서비스로 먼저 구현
2. 페이지네이션 (100건/페이지, 7일 단위 분할)
3. Notice 모델 매핑 (bid_no = `{bid_type}-{fullBidNo}`)
4. bid_no 기준 중복 제거
5. 물품/공사 서비스 추가
6. extra 필드: est_price, budget, bid_method, contract_method, category, contact, attachments

**테스트:**
- 실제 API 호출 통합 테스트 (days=1, 소량)
- Mock 응답 단위 테스트 (XML 파싱, 필드 매핑)
- 빈 응답, 에러 응답 처리

### 1-2. 기업마당 (`bizinfo.py`)

**API:** `https://www.bizinfo.go.kr` 지원사업정보 API

**구현:**
1. API 스펙 확인 (응답 포맷, 필드 목록, 페이지네이션)
2. 전체 공고 수집 (날짜 범위 필터)
3. Notice 모델 매핑 (bid_no = `BIZINFO-{고유ID}`)
4. extra 필드: 분야, 신청기간, 주관기관, 시행기관

**테스트:** 실제 API + Mock 단위

### 1-3. 보조금24 (`subsidy24.py`)

**API:** data.go.kr 대한민국 공공서비스(혜택) 정보 (15113968)

**구현:**
1. API 스펙 확인
2. 전체 수집 + 날짜 필터
3. Notice 모델 매핑
4. 기업 대상 필터링 (시민 복지 항목 제외 옵션)

**테스트:** 실제 API + Mock 단위

### 1-4. K-Startup (`kstartup.py`)

**이식 원본:** lets_portal `collectors/kstartup.py` (dev_reference.md §4)

**변경 사항:**
- 키워드 매칭 제거 → 전체 수집
- 동기(requests) → 비동기(httpx)
- display_settings 테이블 의존성 제거
- only_ongoing 파라미터 유지 (`cond[rcrt_prgs_yn::EQ]=Y`)

**구현:**
1. odcloud 형식 JSON 응답 파싱 (data 배열, totalCount)
2. 접수시작일(pbanc_rcpt_bgng_dt) 기준 cutoff 필터링
3. Notice 모델 매핑 (bid_no = `KSTARTUP-{pbanc_sn}`)
4. extra 필드: target, apply_url, contact, apply_method, biz_year, target_age, department

**테스트:** 실제 API + Mock 단위

### 1-5. 중소벤처기업부 (`smes.py`)

**이식 원본:** lets_portal `collectors/mss_biz.py`

**변경 사항:**
- 키워드 매칭 제거 → 전체 수집
- 동기(requests) → 비동기(httpx)
- XML 파싱: `lxml.etree` 사용 (원본은 `xml.etree.ElementTree`)

**구현:**
1. XML 응답 파싱 (API URL은 HTTP, HTTPS 아님)
2. startDate/endDate 파라미터로 서버 측 날짜 필터링
3. Notice 모델 매핑 (bid_no = `MSS-{itemId}`)
4. 예산 파싱: suptScale에서 정규식으로 숫자 추출
5. 첨부파일: fileName/fileUrl 쌍 복수 추출

**테스트:** 실제 API + Mock 단위

### 1-6. Phase 1 완료 기준

- [x] 5개 수집기 각각 `collect(days=1)` 호출 시 Notice 리스트 반환
- [x] `health_check()` 로 API 연결 확인 가능
- [x] 단위 테스트 통과율 100% (237개)
- [x] 통합 테스트 (실제 API) 통과 (17개)
- [x] `pip install -e .` 로 로컬 설치 후 import 가능
- [x] BidWatch 본체에서 `from bid_collectors import NaraCollector` 동작 확인

---

## Phase 2: generic_scraper 엔진 + 부가 수집기 (2~3주)

> **우선순위 변경:** generic_scraper는 BidWatch의 핵심 기능(AI가 config 생성 → 임의 게시판 자동 수집)이므로
> 확장 수집기보다 앞당겨 구현. 확장 수집기(공기업 API)는 Phase 3으로 이동.

### 2-1. generic_scraper 엔진 (`generic_scraper.py`)

**이식 원본:** lets_portal `collectors/generic_scraper.py` (dev_reference.md §5)

**핵심:** scraper_config JSON을 받아 임의의 게시판을 파싱하는 범용 엔진

**변경 사항:**
- 동기(requests.Session) → 비동기(httpx.AsyncClient)
- `scrape_site(config, days)` → `GenericScraper(config).collect(days)` 클래스로 래핑
- Notice 모델 출력으로 통일
- CCEI/부산/KSD 전용 수집기 → config 기반으로 범용화 검토
- `_parse_date()` → `utils/dates.py` 공유

**config 스키마:** dev_reference.md §6 참조 — 그대로 유지 (AI가 이 포맷으로 생성)

### 2-2. 중소벤처24 (`smes24.py`)

**API:** data.go.kr 공고정보 (15113191)
- 중소벤처기업부(smes.py)와 별개 서비스 — 중소벤처24 포털의 공고 데이터

### 2-3. 나라장터 확장 (`nara.py` 추가 메서드)

기존 nara.py에 메서드 추가:
- `collect_awards()` — 낙찰정보
- `collect_contracts()` — 계약정보
- `collect_pre_specs()` — 사전규격

### 2-4. Phase 2 완료 기준

- [ ] generic_scraper로 lets_portal 48개 사이트 config 중 샘플 5개 테스트 통과
- [ ] 중소벤처24 수집기 동작
- [ ] 나라장터 확장 메서드 동작
- [ ] 패키지 v1.0 릴리스 (pyproject.toml 버전)
- [ ] README.md 사용 가이드 완성

---

## Phase 3: 확장 수집기 — 공기업 API (2~3주)

> 기존 Phase 1 패턴(data.go.kr API)을 그대로 복제. 수요 확인 후 필요한 것부터 구현.

### 3-1. LH 한국토지주택공사 (`lh.py`)

**API 5개 (data.go.kr):**
- 입찰공고정보 (15021183) — 핵심
- 계약현황정보 (15021184)
- 발주계획정보 (15042795)
- 사전규격공개정보 (15042796)
- 개찰정보(예정가격) (15057180)

**구현:**
1. 입찰공고정보 먼저 구현 (나머지는 extra 데이터로 보강)
2. 나라장터에 안 올라오는 LH 자체 입찰건 확보가 핵심
3. bid_no = `LH-{고유번호}`

### 3-2. 한국전력공사 (`kepco.py`)

**API:** 전자입찰계약정보 (15148223)

### 3-3. 한국도로공사 (`kexpressway.py`)

**API:** 전자조달 계약공개현황 (15128076)

### 3-4. 한국수자원공사 (`kwater.py`)

**API:** 전자조달 입찰공고 (15101635)

### 3-5. 방위사업청 (`defense.py`)

**API 5개:**
- 입찰공고 (15002040) — 핵심
- 입찰결과 (15002018)
- 조달계획 (15002017)
- 계약정보 (15002019)
- 코드조회 (15002020)

**구현:** 입찰공고 먼저, 나머지는 extra 보강

### 3-6. Phase 3 완료 기준

- [ ] 5개 확장 수집기 추가, 총 12+ 동작
- [ ] 각 수집기 통합 테스트 통과
- [ ] 수집기별 API 응답 차이 (XML/JSON, 필드명) 정리 문서화

---

## Phase 4: 품질 + 운영 (1~2주)

### 4-1. 에러 처리 / 재시도

- API별 Rate Limit 대응 (429 → exponential backoff)
- 네트워크 타임아웃 재시도 (3회)
- 부분 실패 시 수집된 데이터는 보존 (fail-safe)
- 수집 결과에 에러 정보 포함 (어떤 페이지에서 실패했는지)

### 4-2. 로깅

```python
import logging
logger = logging.getLogger("bid_collectors")

# 수집기별 로그
logger.info(f"[나라장터] 수집 시작: days={days}")
logger.info(f"[나라장터] 수집 완료: {len(notices)}건")
logger.warning(f"[나라장터] 429 에러, {retry}회 재시도")
logger.error(f"[나라장터] 수집 실패: {error}")
```

### 4-3. 모니터링 지원

- `health_check()` — API 키 유효성 + 엔드포인트 응답 확인
- `collect()` 반환값에 메타데이터 추가 옵션:
  ```python
  @dataclass
  class CollectResult:
      notices: list[Notice]
      total_fetched: int
      duration_seconds: float
      errors: list[str]
      pages_processed: int
  ```

### 4-4. Phase 4 완료 기준

- [ ] 모든 수집기 에러 시나리오 테스트 통과
- [ ] 로깅 표준화
- [ ] BidWatch Celery 워커에서 안정적으로 동작 확인
- [ ] 패키지 문서 (API별 필드 매핑표) 완성
