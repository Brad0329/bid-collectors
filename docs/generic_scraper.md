# generic_scraper 엔진 상세 설계

> **버전:** 1.0 (Phase 2 구현용)
> **작성일:** 2026-04-11
> **관련 문서:**
> - [bid_collectors.md](bid_collectors.md) — 패키지 전체 구조
> - [dev_reference.md](dev_reference.md) §5-6 — lets_portal 원본 레퍼런스
> - [plan.md](plan.md) — Phase 2 작업 계획

---

## 1. 개요

### 1-1. 엔진 역할

generic_scraper는 **config JSON을 받아 임의의 HTML 게시판을 파싱하는 범용 수집 엔진**이다.

```
사용자가 URL 등록 → AI가 config 생성 → GenericScraper(config).collect(days=7) → Notice 리스트 반환
```

BidWatch 서비스의 핵심 차별점:
- 공공 API가 없는 사이트도 수집 가능 (지자체, 공기업, 대학, 진흥원 등)
- AI가 자동으로 config를 생성하므로 개발 없이 새 사이트 추가
- lets_portal에서 검증된 39개 사이트 config를 그대로 사용 가능

### 1-2. lets_portal과의 차이

| 항목 | lets_portal | bid-collectors |
|------|-------------|----------------|
| HTTP | `requests.Session` (동기) | `httpx.AsyncClient` (비동기) |
| 진입점 | `scrape_site(config, days)` 함수 | `GenericScraper(config).collect(days)` 클래스 |
| Config | raw dict (검증 없음) | `ScraperConfig` Pydantic 모델 (검증) |
| 출력 | `list[dict]` | `list[Notice]` + `CollectResult` 래핑 |
| 날짜 파서 | 로컬 `_parse_date()` | `utils.dates.parse_date()` (이미 이식됨) |
| 상태 판정 | 로컬 `_get_status()` | `utils.status.determine_status()` (이미 이식됨) |
| DB 저장 | `save_to_db()` 내장 | 제거 — BidWatch 본체 책임 |
| 키워드 매칭 | `_match_keywords()` 내장 | 제거 — BidWatch 조회 시점에서 처리 |
| SSL 검증 | `verify=False` (전역) | `verify=True` 기본, config에서 비활성화 가능 |
| 요청 간격 | 없음 | 기본 0.5초 딜레이 (설정 가능) |

### 1-3. 핵심 설계 결정

1. **BaseCollector 상속**: `__init__` 오버라이드로 api_key 대신 config를 받는다
2. **ScraperConfig Pydantic 모델**: AI 생성 config의 스키마 계약 역할. 검증 + 문서화 + 자동완성
3. **bid_no 호환성**: `SCR-{source_key}-{md5(title+link)[:10]}` 기존 포맷 유지
4. **Phase 2 범위**: HTML 스크래핑 엔진만 구현. CCEI/부산/KSD 등 JSON API 전용 수집기는 제외

---

## 2. 아키텍처

### 2-1. 클래스 계층

```
BaseCollector (ABC)
├── NaraCollector      (api_key 필수)
├── BizinfoCollector   (api_key 필수)
├── Subsidy24Collector (api_key 필수)
├── KstartupCollector  (api_key 필수)
├── SmesCollector      (api_key 필수)
└── GenericScraper     (config 필수, api_key 불필요)
```

GenericScraper는 BaseCollector를 상속하되 `__init__`을 오버라이드하여 api_key 요구를 우회한다:

```python
class GenericScraper(BaseCollector):
    source_name = "scraper"  # config.name으로 동적 설정

    def __init__(self, config: ScraperConfig | dict, **kwargs):
        if isinstance(config, dict):
            config = ScraperConfig(**config)
        self.config = config
        self.source_name = config.name
        self.api_key = None  # API 키 불필요
```

`collect()` 메서드는 BaseCollector의 템플릿 메서드를 **그대로 상속**:
- `_fetch()` 호출 → `(list[Notice], int)` 반환
- `(source, bid_no)` 기준 중복 제거
- `CollectResult` 래핑

### 2-2. 파일 구조

```
bid_collectors/
├── generic_scraper.py    # ScraperConfig 모델 + GenericScraper 클래스 (단일 파일)
└── __init__.py           # GenericScraper, ScraperConfig export 추가

tests/
└── test_generic_scraper.py
```

config 모델과 엔진 클래스는 긴밀하게 결합되어 있으므로 단일 파일로 유지.
기존 수집기(nara.py, bizinfo.py 등)도 모두 단일 파일 패턴이므로 일관성 유지.

---

## 3. ScraperConfig 모델

### 3-1. 전체 스키마

```python
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal

class ScraperConfig(BaseModel):
    """Config 기반 HTML 스크래퍼 설정.

    AI가 생성하거나 수동으로 작성한 config를 검증하는 스키마.
    model_json_schema()로 AI 프롬프트에 사용할 JSON Schema 추출 가능.
    """

    # ──── 필수 필드 ────
    name: str = Field(..., min_length=1,
        description="사이트/기관 표시명. source 필드 + organization으로 사용")
    source_key: str = Field(..., min_length=1, max_length=30, pattern=r'^[a-z0-9_]+$',
        description="영문 소문자 키. bid_no 생성용 (SCR-{source_key}-{hash})")
    list_url: str = Field(...,
        description="게시판 목록 페이지 URL")
    list_selector: str = Field(...,
        description="행(row) CSS 셀렉터. 예: 'table tbody tr', 'div.item'")
    title_selector: str = Field(...,
        description="행 내 제목 요소 CSS 셀렉터. 예: 'td:nth-child(2) a'")
    date_selector: str = Field(...,
        description="행 내 날짜 요소 CSS 셀렉터. 예: 'td:nth-child(5)'")

    # ──── 공통 선택 필드 ────
    link_attr: str = Field(default="href",
        description="링크를 가져올 HTML 속성. 기본 href")
    link_base: str = Field(default="",
        description="상대 URL 변환용 base URL. 비어있으면 list_url 사용")
    pagination: str = Field(default="",
        description="페이지네이션 URL 접미사. {page} 또는 {offset} 플레이스홀더 사용. "
                    "예: '&page={page}', '&pager.offset={offset}'. "
                    "빈 문자열이면 페이지네이션 없음 (1페이지만)")
    max_pages: int = Field(default=3, ge=1, le=50,
        description="최대 수집 페이지 수")
    encoding: str = Field(default="utf-8",
        description="응답 인코딩")
    parser: Literal["html.parser", "lxml", "html5lib"] = Field(default="html.parser",
        description="BeautifulSoup 파서")

    # ──── Offset 기반 페이지네이션 ────
    offset_size: int = Field(default=0, ge=0,
        description="offset 기반 페이지네이션 시 페이지당 항목 수. "
                    "pagination에 {offset}이 있을 때만 사용")

    # ──── JavaScript 링크 추출 ────
    link_js_regex: str = Field(default="",
        description="JS onclick/href에서 ID 추출 정규식. "
                    r"예: fncShow\('(\d+)'\)")
    link_template: str = Field(default="",
        description="추출된 ID로 URL 생성 템플릿. "
                    "{id} = 첫 번째 그룹, {1}/{2}/{3} = 번호별 그룹. "
                    "예: '/detail.do?seq={id}'")

    # ──── 세션/쿠키 ────
    session_init_url: str = Field(default="",
        description="쿠키 획득용 사전 요청 URL. 세션 인증이 필요한 사이트에 사용")

    # ──── POST 요청 ────
    post_data: dict | None = Field(default=None,
        description="POST 요청 데이터. None이면 GET 사용. "
                    "빈 dict({})도 POST 트리거")
    post_json: bool = Field(default=False,
        description="True면 JSON body, False면 form-encoded")
    page_param_key: str = Field(default="",
        description="post_data 내 페이지 번호 키. 예: 'pageIndex'")

    # ──── 컨테이너 격리 ────
    grid_selector: str = Field(default="",
        description="데이터 영역 CSS 셀렉터. 설정하면 이 영역 내에서만 list_selector 적용")

    # ──── 동작 플래그 ────
    skip_no_date: bool = Field(default=True,
        description="True면 날짜 파싱 실패 행을 건너뜀. False면 날짜 없이도 수집")
    verify_ssl: bool = Field(default=True,
        description="False면 SSL 인증서 검증 비활성화 (인증서 문제 사이트용)")

    # ──── 검증 규칙 ────

    @field_validator('source_key')
    @classmethod
    def source_key_ascii(cls, v: str) -> str:
        if not v.isascii():
            raise ValueError('source_key는 ASCII 문자만 허용')
        return v.lower()

    @model_validator(mode='after')
    def validate_js_link_pair(self) -> 'ScraperConfig':
        """link_js_regex와 link_template은 반드시 함께 사용."""
        if bool(self.link_js_regex) != bool(self.link_template):
            raise ValueError('link_js_regex와 link_template은 반드시 함께 설정해야 합니다')
        return self

    @model_validator(mode='after')
    def validate_post_pagination(self) -> 'ScraperConfig':
        """page_param_key는 post_data가 있을 때만 사용 가능."""
        if self.page_param_key and self.post_data is None:
            raise ValueError('page_param_key를 사용하려면 post_data가 필요합니다')
        return self

    @model_validator(mode='after')
    def validate_offset_pagination(self) -> 'ScraperConfig':
        """offset_size는 pagination에 {offset}이 있을 때만 유효."""
        if self.offset_size > 0 and '{offset}' not in self.pagination:
            raise ValueError('offset_size를 사용하려면 pagination에 {offset} 플레이스홀더가 필요합니다')
        return self
```

### 3-2. 필드 사용 통계 (lets_portal 39개 config 기준)

| 필드 | 사용 빈도 | 비고 |
|------|-----------|------|
| name, source_key, list_url | 39/39 (100%) | 필수 |
| list_selector, title_selector, date_selector | 39/39 (100%) | 필수 |
| link_attr, link_base, encoding, max_pages | 39/39 (100%) | 모든 config에 명시 |
| pagination | 32/39 (82%) | 7개 사이트는 1페이지만 |
| link_js_regex + link_template | 4/39 (10%) | itp, konkuk, gntp, koipa |
| post_data + page_param_key | 2/39 (5%) | itp (form), gntp (JSON) |
| session_init_url | 1/39 (3%) | itp만 |
| post_json | 1/39 (3%) | gntp만 |
| grid_selector | 1/39 (3%) | gntp만 |
| skip_no_date (=false) | 1/39 (3%) | ofjeju만 |
| offset_size | 1/39 (3%) | kipa만 |
| parser (!=html.parser) | 1/39 (3%) | ctp (lxml) |

### 3-3. 사이트 유형별 config 예시

#### (1) 기본형 — GET + 테이블 (가장 흔한 패턴, 31개 사이트)

```json
{
  "name": "한국콘텐츠진흥원",
  "source_key": "kocca",
  "list_url": "https://www.kocca.kr/kocca/tender/list.do?menuNo=204106&cate=01",
  "list_selector": "table tbody tr",
  "title_selector": "td:nth-child(2) a",
  "date_selector": "td:nth-child(5)",
  "link_attr": "href",
  "link_base": "https://www.kocca.kr",
  "pagination": "&pageIndex={page}",
  "max_pages": 3,
  "encoding": "utf-8"
}
```

#### (2) POST form 기반 + 세션 쿠키 + JS 링크

```json
{
  "name": "인천테크노파크",
  "source_key": "itp",
  "list_url": "https://www.itp.or.kr/intro.asp",
  "list_selector": "table.board_list tbody tr",
  "title_selector": "td.subject a",
  "date_selector": "td:nth-child(4)",
  "session_init_url": "https://www.itp.or.kr/",
  "post_data": {"search": "1", "tmid": "14", "bid": "2", "PageShowSize": "10"},
  "page_param_key": "PageNum",
  "link_js_regex": "fncShow\\('(\\d+)'\\)",
  "link_template": "/intro.asp?tmid=14&seq={id}",
  "link_attr": "href",
  "link_base": "https://www.itp.or.kr",
  "encoding": "utf-8",
  "max_pages": 3
}
```

#### (3) POST JSON + grid 셀렉터

```json
{
  "name": "경남테크노파크",
  "source_key": "gntp",
  "list_url": "http://account.more.co.kr/api/orderalim/list",
  "list_selector": "#gridData tr",
  "title_selector": "td:nth-child(2)",
  "date_selector": "td:nth-child(5)",
  "post_data": {"pageSize": 20, "searchType": ""},
  "post_json": true,
  "page_param_key": "pageIndex",
  "grid_selector": "#gridData",
  "link_js_regex": "fn_view\\('(\\d+)'\\)",
  "link_template": "http://account.more.co.kr/contract/orderalim_view.php?idx={id}",
  "link_attr": "href",
  "link_base": "",
  "encoding": "utf-8",
  "max_pages": 3
}
```

#### (4) 비테이블 레이아웃 (div/ul 기반)

```json
{
  "name": "제주콘텐츠진흥원",
  "source_key": "ofjeju",
  "list_url": "https://ofjeju.kr/communication/notifications.htm",
  "list_selector": "div.app-list div.item",
  "title_selector": "a.tit",
  "date_selector": "span.date",
  "link_attr": "href",
  "link_base": "https://ofjeju.kr",
  "skip_no_date": false,
  "encoding": "utf-8",
  "max_pages": 1
}
```

#### (5) Offset 기반 페이지네이션

```json
{
  "name": "한국발명진흥회",
  "source_key": "kipa",
  "list_url": "https://www.kipa.org/kipa/bid/bidList.do",
  "list_selector": "table tbody tr",
  "title_selector": "td:nth-child(2) a",
  "date_selector": "td:nth-child(5)",
  "pagination": "&pager.offset={offset}",
  "offset_size": 10,
  "link_attr": "href",
  "link_base": "https://www.kipa.org",
  "encoding": "utf-8",
  "max_pages": 3
}
```

#### (6) 다중 그룹 JS 링크 (3개 캡처 그룹)

```json
{
  "name": "건국대학교",
  "source_key": "konkuk",
  "list_url": "https://www.konkuk.ac.kr/bid/list.do",
  "list_selector": "table tbody tr",
  "title_selector": "td:nth-child(2) a",
  "date_selector": "td:nth-child(4)",
  "link_js_regex": "jf_artclView\\('(\\w+)',\\s*'(\\w+)',\\s*'(\\d+)'\\)",
  "link_template": "https://www.konkuk.ac.kr/bid/{1}/{3}/{2}View",
  "link_attr": "href",
  "link_base": "https://www.konkuk.ac.kr",
  "encoding": "utf-8",
  "max_pages": 3
}
```

---

## 4. GenericScraper 클래스

### 4-1. 클래스 구조

```
GenericScraper(BaseCollector)
├── __init__(config)              config 검증, source_name 동적 설정
├── _fetch(days, **kwargs)        [추상 구현] 메인 수집 루프
├── health_check()                사이트 접근 가능 여부 확인
│
├── _fetch_page(client, page)     단일 페이지 요청 (GET/POST)
├── _build_page_url(page)         페이지네이션 URL 구성
├── _parse_rows(html, cutoff)     HTML → Notice 리스트 변환
├── _extract_link(title_el)       제목 요소에서 링크 추출 (JS regex 포함)
└── _make_bid_no(title, link)     결정적 bid_no 해시 생성
```

### 4-2. 메서드 시그니처 상세

```python
class GenericScraper(BaseCollector):
    source_name = "scraper"

    def __init__(self, config: ScraperConfig | dict, **kwargs):
        """config 검증 및 초기화. API 키 불필요.

        Args:
            config: ScraperConfig 인스턴스 또는 raw dict (자동 검증)

        Raises:
            pydantic.ValidationError: config 검증 실패
        """

    async def _fetch(self, days: int = 30, **kwargs) -> tuple[list[Notice], int]:
        """설정된 사이트에서 공고 수집.

        Args:
            days: 수집 기간 (일). cutoff = now - timedelta(days)
            **kwargs:
                max_pages: int — config의 max_pages 오버라이드
                delay: float — 페이지 간 요청 간격(초). 기본 0.5

        Returns:
            (notices 리스트, 처리된 페이지 수)
        """

    async def health_check(self) -> dict:
        """1페이지 접근 테스트.

        Returns:
            {"status": "ok"|"error", "source": ..., "response_time_ms": ...}
        """

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        page: int,
    ) -> httpx.Response:
        """단일 페이지 HTTP 요청.

        GET 또는 POST를 config에 따라 분기.
        POST 시 page_param_key로 페이지 번호 주입.

        Raises:
            httpx.HTTPStatusError: 4xx/5xx 응답
        """

    def _build_page_url(self, page: int) -> str:
        """페이지네이션 패턴에 따라 URL 구성.

        page=1이거나 pagination이 비어있으면 list_url 그대로 반환.
        {page} 또는 {offset} 플레이스홀더를 치환.
        """

    def _parse_rows(
        self,
        html: str,
        cutoff: datetime,
    ) -> tuple[list[Notice], bool]:
        """HTML을 파싱하여 Notice 리스트 반환.

        Args:
            html: 페이지 HTML 문자열
            cutoff: 이 시각보다 오래된 공고는 스킵

        Returns:
            (notices 리스트, cutoff 이전 항목 존재 여부)

        개별 행 파싱 실패는 로깅 후 건너뜀 (전체 실패하지 않음).
        """

    def _extract_link(self, title_el: Tag) -> str:
        """제목 요소에서 링크 추출.

        1. title_el이 <a>가 아니면 하위 <a> 탐색
        2. link_attr 속성에서 원본 링크 추출
        3. link_js_regex가 설정되어 있으면 정규식으로 ID 추출 후 link_template에 치환
        4. 상대 URL → 절대 URL 변환 (link_base 또는 list_url 기준)

        Returns:
            절대 URL 문자열. 추출 실패 시 빈 문자열.
        """

    def _make_bid_no(self, title: str, link: str) -> str:
        """결정적 bid_no 생성.

        포맷: SCR-{source_key}-{md5(title.strip() + link.strip())[:10]}
        """
```

### 4-3. `_fetch` 흐름도

```
_fetch(days=30)
│
├── max_pages = kwargs.get("max_pages", config.max_pages)
├── delay = kwargs.get("delay", 0.5)
├── cutoff = (now - timedelta(days)).replace(hour=0, minute=0, second=0, microsecond=0)
│
├── create_client(timeout=15.0, verify=config.verify_ssl)
│   │
│   ├── [session_init_url이 있으면]
│   │   └── await client.get(session_init_url)  # 쿠키 획득
│   │
│   └── for page in range(1, max_pages + 1):
│       │
│       ├── try:
│       │   └── resp = await _fetch_page(client, page)
│       ├── except httpx.HTTPError:
│       │   └── logger.warning → break (부분 수집)
│       │
│       ├── [인코딩 처리]
│       │   ├── config.encoding != "utf-8" → resp.content.decode(encoding)
│       │   └── "utf-8" → resp.text (httpx 자동 감지)
│       │
│       ├── page_notices, has_old = _parse_rows(html, cutoff)
│       ├── all_notices.extend(page_notices)
│       ├── pages_processed += 1
│       │
│       ├── [종료 조건]
│       │   ├── has_old and not page_notices → break (오래된 공고만)
│       │   └── not page_notices → break (빈 페이지)
│       │
│       └── if page < max_pages:
│           └── await asyncio.sleep(delay)  # 요청 간격
│
└── return (all_notices, pages_processed)
```

### 4-4. `_parse_rows` 상세 흐름

```
_parse_rows(html, cutoff)
│
├── soup = BeautifulSoup(html, config.parser)
│
├── [grid_selector가 있으면]
│   ├── container = soup.select_one(grid_selector)
│   ├── container가 없으면 → return ([], False)
│   └── rows = container.select(list_selector)
├── [없으면]
│   └── rows = soup.select(list_selector)
│
├── rows가 비어있으면 → return ([], False)
│
├── notices = []
├── has_old = False
│
├── for row in rows:
│   │
│   ├── try:  ── 행 단위 에러 격리 ──
│   │
│   ├── [제목 추출]
│   │   ├── title_el = row.select_one(title_selector)
│   │   ├── title_el이 없으면 → continue
│   │   └── title = title_el.get_text(strip=True)
│   │
│   ├── [날짜 추출]
│   │   ├── date_el = row.select_one(date_selector)
│   │   ├── date_text = date_el.get_text(strip=True) if date_el else ""
│   │   ├── parsed_date = parse_date(date_text)  # utils.dates
│   │   ├── parsed_date가 None이고 skip_no_date=True → continue
│   │   └── cutoff 비교:
│   │       ├── parsed_date가 cutoff 이전 → has_old = True, continue
│   │       └── 파싱 실패 → 무시하고 계속
│   │
│   ├── [링크 추출]
│   │   └── detail_url = _extract_link(title_el)
│   │
│   ├── [Notice 생성]
│   │   └── Notice(
│   │       source=config.name,
│   │       bid_no=_make_bid_no(title, detail_url),
│   │       title=title,
│   │       organization=config.name,
│   │       start_date=parsed_date (date 변환),
│   │       end_date=None,
│   │       status=determine_status(parsed_date),
│   │       url=detail_url,
│   │       detail_url=detail_url,
│   │   )
│   │
│   ├── notices.append(notice)
│   │
│   └── except Exception:  ── 행 파싱 실패 → 로깅 후 건너뜀 ──
│
└── return (notices, has_old)
```

### 4-5. 페이지네이션 엔진

lets_portal 39개 config에서 발견된 4가지 패턴을 모두 지원:

#### 패턴 1: URL 접미사 + `{page}` (32개 사이트)

```python
# config: "pagination": "&pageIndex={page}"
# page=3 → list_url + "&pageIndex=3"
url = list_url + pagination.replace("{page}", str(page))
```

#### 패턴 2: URL 접미사 + `{offset}` (1개 사이트: kipa)

```python
# config: "pagination": "&pager.offset={offset}", "offset_size": 10
# page=3 → offset = (3-1) * 10 = 20 → list_url + "&pager.offset=20"
offset = (page - 1) * config.offset_size
url = list_url + pagination.replace("{offset}", str(offset))
```

#### 패턴 3: POST + page_param_key (2개 사이트: itp, gntp)

```python
# config: "post_data": {...}, "page_param_key": "PageNum"
# page=3 → post_data["PageNum"] = 3
form = dict(config.post_data)
form[config.page_param_key] = page
# post_json=True → client.post(url, json=form)
# post_json=False → client.post(url, data=form)
```

#### 패턴 4: 페이지네이션 없음 (7개 사이트)

```python
# config: "pagination": "", "max_pages": 1
# page=1 → list_url 그대로, page>1 → 루프 종료
```

#### `_build_page_url` 구현

```python
def _build_page_url(self, page: int) -> str:
    if page == 1 or not self.config.pagination:
        return self.config.list_url

    pagination = self.config.pagination

    if self.config.offset_size and "{offset}" in pagination:
        offset = (page - 1) * self.config.offset_size
        return self.config.list_url + pagination.replace("{offset}", str(offset))

    return self.config.list_url + pagination.replace("{page}", str(page))
```

#### `_fetch_page` 구현

```python
async def _fetch_page(self, client: httpx.AsyncClient, page: int) -> httpx.Response:
    if self.config.post_data is not None:
        form = dict(self.config.post_data)
        if self.config.page_param_key:
            form[self.config.page_param_key] = page
        if self.config.post_json:
            resp = await client.post(self.config.list_url, json=form)
        else:
            resp = await client.post(self.config.list_url, data=form)
    else:
        url = self._build_page_url(page)
        resp = await client.get(url)

    resp.raise_for_status()
    return resp
```

### 4-6. 링크 추출

```python
def _extract_link(self, title_el: Tag) -> str:
    # 1. <a> 요소 탐색
    link_el = title_el if title_el.name == "a" else title_el.find("a")
    if not link_el:
        return ""

    # 2. 원본 링크 추출
    raw_link = link_el.get(self.config.link_attr, "")
    if not raw_link:
        return ""

    # 3. JS regex 추출 (설정된 경우)
    if self.config.link_js_regex and self.config.link_template:
        match = re.search(self.config.link_js_regex, raw_link)
        if match:
            result = self.config.link_template
            # {id} = 첫 번째 그룹 (호환성)
            result = result.replace("{id}", match.group(1))
            # {1}, {2}, {3} = 번호별 그룹 (다중 그룹 지원)
            for i in range(1, len(match.groups()) + 1):
                result = result.replace(f"{{{i}}}", match.group(i))
            raw_link = result

    # 4. 절대 URL 변환
    if raw_link and not raw_link.startswith(("http://", "https://")):
        base = self.config.link_base or self.config.list_url
        raw_link = urljoin(base, raw_link)

    return raw_link
```

**JS 링크 추출 지원 사이트 (4개):**

| 사이트 | regex | template | 그룹 수 |
|--------|-------|----------|---------|
| 인천테크노파크 (itp) | `fncShow\('(\d+)'\)` | `/intro.asp?tmid=14&seq={id}` | 1 |
| 한국지식재산보호원 (koipa) | `pageviewform\('(\d+)'\)` | `...brdDetail.do?...num={id}` | 1 |
| 경남테크노파크 (gntp) | `(/biz/applyInfo/\d+)` | `https://www.gntp.or.kr{id}` | 1 |
| 건국대학교 (konkuk) | `jf_artclView\('(\w+)',\s*'(\w+)',\s*'(\d+)'\)` | `.../bid/{1}/{3}/{2}View` | 3 |

### 4-7. bid_no 생성

```python
def _make_bid_no(self, title: str, link: str) -> str:
    hash_input = f"{title.strip()}{link.strip()}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:10]
    return f"SCR-{self.config.source_key}-{hash_val}"
```

**포맷:** `SCR-{source_key}-{md5 10자리}`
- 예: `SCR-kocca-a3f2b1c9d4`
- lets_portal과 동일한 포맷으로 하위 호환성 유지
- 입력값 strip으로 공백 차이에 의한 중복 방지

**알려진 제약:**
- 사이트가 제목을 살짝 변경하면 hash가 달라져 중복 발생 가능
- 향후 개선: `bid_no_regex` config 필드로 URL에서 안정적 ID 추출 (§8-2 참조)

### 4-8. 쿠키/세션 처리

httpx.AsyncClient는 `async with` 블록 내에서 쿠키를 자동 유지:

```python
async with create_client(timeout=15.0) as client:
    # 1. 세션 초기화 (쿠키 획득)
    if self.config.session_init_url:
        await client.get(self.config.session_init_url)
        # → client.cookies에 세션 쿠키 자동 저장

    # 2. 이후 요청에서 쿠키 자동 전송
    for page in range(1, max_pages + 1):
        resp = await self._fetch_page(client, page)
        # → 쿠키가 자동으로 포함됨
```

requests.Session과 동일한 동작. 추가 구현 불필요.

### 4-9. 인코딩 처리

```python
# 응답 텍스트 추출
if self.config.encoding.lower() != "utf-8":
    text = resp.content.decode(self.config.encoding, errors="replace")
else:
    text = resp.text  # httpx 자동 감지 (Content-Type charset 또는 UTF-8)
```

lets_portal 원본의 `resp.apparent_encoding` 방식은 때때로 오감지 문제가 있었음.
httpx는 Content-Type 헤더의 charset을 우선하므로 더 안정적.

---

## 5. 에러 처리 / 복원력

### 5-1. 에러 그래뉼래리티 (4단계)

| 단계 | 범위 | 처리 | 결과 |
|------|------|------|------|
| **Config 검증** | 전체 | `ValidationError` 발생 | 생성자에서 즉시 실패 (fail-fast) |
| **페이지 요청** | 단일 페이지 | `logger.warning` + `break` | 수집된 데이터까지 반환 (부분 수집) |
| **행 파싱** | 단일 행 | `logger.debug` + `continue` | 해당 행 건너뛰고 계속 |
| **필드 파싱** | 단일 필드 | 기본값 사용 | 날짜=None, 링크="" 등 |

### 5-2. 에러 흐름

```
GenericScraper(invalid_config)
  → ValidationError (즉시 실패, 수집 시도 안 함)

GenericScraper(valid_config).collect(days=7)
  → _fetch() 호출
    → page 1: 성공 → 10건 수집
    → page 2: 500 에러 → logger.warning, break
    → return (10건, 1페이지)  # 부분 수집
  → BaseCollector.collect()
    → CollectResult(notices=10건, is_partial=False, errors=[])
    # 주의: 페이지 레벨 에러는 _fetch 내부에서 처리되므로
    # BaseCollector의 try-except에 도달하지 않음

GenericScraper(valid_config).collect(days=7)
  → _fetch() 호출
    → create_client() 자체 실패 → Exception 발생
  → BaseCollector.collect() catch
    → CollectResult(notices=[], is_partial=True, errors=["..."])
```

### 5-3. 로깅

```python
import logging
logger = logging.getLogger("bid_collectors")

# 레벨별 사용 기준
logger.info(f"[{source_name}] 수집 시작: days={days}, max_pages={max_pages}")
logger.info(f"[{source_name}] 수집 완료: {len(notices)}건, {pages}페이지")
logger.warning(f"[{source_name}] 페이지 {page} 요청 실패: {error}")
logger.debug(f"[{source_name}] 행 파싱 스킵: {reason}")
```

---

## 6. 테스트 전략

### 6-1. 단위 테스트 (네트워크 없음)

**파일:** `tests/test_generic_scraper.py`

#### ScraperConfig 검증 (12개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 최소 필수 필드만으로 생성 | 6개 필수 필드 + 기본값 |
| 2 | 전체 필드 설정 | 모든 선택 필드 포함 |
| 3 | 필수 필드 누락 → ValidationError | name, source_key 등 각각 |
| 4 | source_key 패턴 검증 | 대문자, 한국어, 특수문자 거부 |
| 5 | source_key 자동 소문자 변환 | "KOCCA" → "kocca" |
| 6 | link_js_regex만 설정 (template 없음) | ValidationError |
| 7 | link_template만 설정 (regex 없음) | ValidationError |
| 8 | page_param_key without post_data | ValidationError |
| 9 | offset_size without {offset} | ValidationError |
| 10 | max_pages 범위 검증 | 0, 51 거부 |
| 11 | parser 허용값 검증 | html.parser, lxml, html5lib만 |
| 12 | 39개 프로덕션 config 전체 검증 | parametrize로 모두 통과 확인 |

#### GenericScraper 초기화 (5개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | ScraperConfig 인스턴스로 생성 | source_name 설정 |
| 2 | raw dict로 생성 (자동 검증) | 동일 동작 |
| 3 | 잘못된 dict → ValidationError | 필수 필드 누락 |
| 4 | api_key가 None | 에러 없음 |
| 5 | source_name이 config.name과 동일 | 동적 설정 확인 |

#### 링크 추출 (8개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 직접 href 추출 | 표준 `<a href="...">` |
| 2 | JS onclick 단일 그룹 | `fncShow('123')` → template 치환 |
| 3 | JS onclick 다중 그룹 | 건국대 패턴 {1},{2},{3} |
| 4 | 상대 URL → 절대 URL (link_base) | `/path` → `https://base/path` |
| 5 | 상대 URL → 절대 URL (list_url fallback) | link_base 미설정 시 |
| 6 | 링크 없는 요소 | 빈 문자열 반환 |
| 7 | `javascript:void(0)` | 그대로 반환 (JS regex 미설정 시) |
| 8 | title_el이 `<a>`가 아닌 경우 | 하위 `<a>` 탐색 |

#### 행 파싱 (10개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 표준 테이블 행 파싱 | `table tbody tr` |
| 2 | div/ul 레이아웃 파싱 | `div.item` |
| 3 | 날짜 파싱 + cutoff 필터링 | 오래된 행 제외, has_old 플래그 |
| 4 | skip_no_date=True | 날짜 없는 행 스킵 |
| 5 | skip_no_date=False | 날짜 없어도 수집 |
| 6 | grid_selector 적용 | 컨테이너 내부만 파싱 |
| 7 | 제목 없는 행 스킵 | title_el이 None |
| 8 | bid_no 생성 확인 | SCR-{key}-{hash} 포맷 |
| 9 | Notice 필드 매핑 | source, organization = config.name |
| 10 | 행 파싱 예외 → 건너뜀 | try-except 동작 |

#### 페이지네이션 URL (6개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | page=1 → list_url 그대로 | 접미사 없음 |
| 2 | {page} 치환 | page=3 → "&page=3" |
| 3 | {offset} 치환 | page=3, offset_size=10 → offset=20 |
| 4 | 페이지네이션 없음 + page>1 | list_url 반환 |
| 5 | POST 모드 URL 무시 | post_data 설정 시 |
| 6 | 다양한 pagination 패턴 | `?page=`, `&pageIndex=`, `&nPage=` |

#### bid_no 생성 (3개 예상)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 동일 입력 → 동일 출력 | 결정적 |
| 2 | 다른 입력 → 다른 출력 | 충돌 없음 |
| 3 | 포맷 검증 | `SCR-{key}-{10자리 hex}` |

### 6-2. 통합 테스트 (respx mock)

`respx`로 httpx를 모킹하여 전체 흐름 테스트:

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | 단일 페이지 GET 스크래핑 | HTML mock → Notice 출력 |
| 2 | 다중 페이지 페이지네이션 | 2페이지 mock → 2페이지 모두 처리 |
| 3 | POST 요청 | POST body 정상 전송 |
| 4 | 세션 초기화 | session_init_url 사전 요청 확인 |
| 5 | 오래된 컨텐츠 조기 종료 | page 2 cutoff → 1페이지만 |
| 6 | HTTP 에러 → 부분 수집 | 500 응답, 기존 수집분 반환 |
| 7 | 빈 페이지 | 행 없는 HTML → 빈 결과 |

### 6-3. 실제 사이트 통합 테스트

`@pytest.mark.integration` 마킹, 안정적인 2-3개 사이트로 검증:

```python
@pytest.mark.integration
async def test_real_site_kocca():
    """한국콘텐츠진흥원 실제 스크래핑 — 안정적인 정부 사이트."""
    config = {...}  # kocca config
    scraper = GenericScraper(config)
    result = await scraper.collect(days=30)
    assert len(result.notices) > 0
    assert result.notices[0].bid_no.startswith("SCR-kocca-")
```

---

## 7. lets_portal 마이그레이션 노트

### 7-1. 변경 항목

| 항목 | lets_portal | bid-collectors | 이유 |
|------|-------------|----------------|------|
| HTTP 클라이언트 | `requests.Session` (동기) | `httpx.AsyncClient` (비동기) | 전체 패키지 비동기 설계 |
| 진입점 | `scrape_site(config, days)` 함수 | `GenericScraper(config).collect(days)` | BaseCollector 패턴 통일 |
| Config 검증 | 없음 (raw dict) | ScraperConfig Pydantic 모델 | AI 생성 config 안전성 |
| 출력 | `list[dict]` | `list[Notice]` + `CollectResult` | 타입 안전성 |
| 날짜 파서 | 로컬 `_parse_date()` | `utils.dates.parse_date()` | 유틸리티 통합 (이미 이식) |
| 상태 판정 | 로컬 `_get_status()` | `utils.status.determine_status()` | 유틸리티 통합 (이미 이식) |
| DB 저장 | `save_to_db()` 내장 | 제거 | BidWatch 본체 책임 |
| 키워드 매칭 | `_match_keywords()` 내장 | 제거 | BidWatch 조회 시점에서 |
| SSL 검증 | `verify=False` (전역) | `verify=True` 기본 + config 설정 | 보안 강화 |
| 요청 간격 | 없음 | 0.5초 기본 딜레이 | 사이트 부하 방지 |
| 전용 수집기 | CCEI/부산/KSD 하드코딩 | Phase 2에서 제외 | Phase 3 JSON API 모드로 |

### 7-2. 유지 항목

| 항목 | 설명 |
|------|------|
| Config JSON 필드명 | name, source_key, list_url 등 모두 동일 |
| bid_no 포맷 | `SCR-{source_key}-{md5[:10]}` 그대로 |
| 39개 config | scraper_configs.json 수정 없이 사용 가능 |
| 페이지네이션 로직 | page/offset 패턴 동일 |
| JS 링크 추출 | regex + template 치환 동일 |
| 행 파싱 순서 | title → date → link → notice 동일 |
| 조기 종료 조건 | 오래된 공고 발견 시 다음 페이지 중단 |

### 7-3. SSL 검증 주의

lets_portal은 모든 요청에 `verify=False`. bid-collectors는 `verify=True` 기본이므로, SSL 인증서 문제가 있는 사이트(특히 `http://` URL 사용 사이트)는 config에 `verify_ssl: false`를 추가해야 할 수 있다.

해당 가능성이 있는 사이트:
- `http://account.more.co.kr` (gntp) — HTTP
- `http://www.cba.ne.kr` (cba) — HTTP
- 기타 인증서 만료/자체서명 사이트

---

## 8. 향후 확장 로드맵

### 8-1. JSON API 모드 (Phase 3)

CCEI/부산/KSD 같은 JSON API 사이트를 config로 범용화:

```python
# ScraperConfig 확장 필드
api_mode: Literal["html", "json"] = "html"
json_list_path: str = ""      # 응답 JSON에서 항목 배열 경로. 예: "result.list"
json_title_key: str = ""      # 항목 내 제목 키. 예: "TITLE"
json_date_key: str = ""       # 항목 내 날짜 키. 예: "REG_DATE"
json_link_key: str = ""       # 항목 내 링크 키. 예: "SEQ"
json_link_prefix: str = ""    # 링크 prefix. 예: "https://site.kr/view?id="
json_id_key: str = ""         # bid_no용 고유 ID 키. 예: "SEQ"
```

**GenericScraper 분기:**

```python
async def _fetch(self, days=30, **kwargs):
    if self.config.api_mode == "json":
        return await self._fetch_json(days, **kwargs)
    else:
        return await self._fetch_html(days, **kwargs)
```

**CCEI config 예시 (JSON API 모드):**

```json
{
  "name": "CCEI 경기",
  "source_key": "ccei_gyeonggi",
  "api_mode": "json",
  "list_url": "https://ccei.creativekorea.or.kr/gyeonggi/allim/allimList.json",
  "post_data": {"div_code": "2"},
  "post_json": false,
  "page_param_key": "pn",
  "json_list_path": "result.list",
  "json_title_key": "TITLE",
  "json_date_key": "REG_DATE",
  "json_id_key": "SEQ",
  "json_link_prefix": "https://ccei.creativekorea.or.kr/gyeonggi/allim/allimView.do?no=",
  "max_pages": 3
}
```

### 8-2. 안정적 bid_no 추출 (Phase 3)

URL에서 안정적 ID를 추출하여 md5 해시 대신 사용:

```python
# ScraperConfig 확장 필드
bid_no_regex: str = ""  # detail URL에서 ID 추출 정규식
                        # 예: "seq=(\d+)", "no=(\d+)", "/(\d+)$"
```

**동작:**

```python
def _make_bid_no(self, title: str, link: str) -> str:
    if self.config.bid_no_regex and link:
        match = re.search(self.config.bid_no_regex, link)
        if match:
            return f"SCR-{self.config.source_key}-{match.group(1)}"
    # fallback: 기존 md5 해시
    hash_input = f"{title.strip()}{link.strip()}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:10]
    return f"SCR-{self.config.source_key}-{hash_val}"
```

### 8-3. 상세 페이지 스크래핑 (Phase 3+)

목록 페이지에서 수집한 URL로 상세 페이지까지 방문하여 추가 정보 추출:

```python
# ScraperConfig 확장 필드
detail_selectors: dict | None = None
# 예시:
# {
#   "content": "div.article-body",
#   "budget": "td.budget",
#   "end_date": "span.deadline",
#   "attachments": "a.file-download"
# }
```

**구현 고려사항:**
- 상세 페이지 요청은 동시성 제한 필요 (`asyncio.Semaphore(3)` 등)
- 요청 간격도 적용 (사이트 부하 방지)
- 상세 페이지 실패 시 목록 정보만으로 Notice 생성 (graceful degradation)
- 대량 공고 수집 시 상세 페이지 요청이 병목 → 선택적 활성화 필요

### 8-4. AI Config 생성 연동 (Phase 3+)

BidWatch의 AI 파이프라인에서 ScraperConfig를 자동 생성:

```
1. 사용자: URL 입력 ("https://www.kocca.kr/kocca/tender/list.do")
2. BidWatch: AI에게 URL + ScraperConfig JSON Schema 전달
3. AI: 페이지 HTML 분석 → ScraperConfig JSON 생성
4. BidWatch: ScraperConfig(**ai_output) 검증
5. BidWatch: GenericScraper(config).collect(days=7) 테스트 실행
6. 결과 확인 → 성공 시 scraper_registry에 저장
```

**Pydantic의 역할:**
- `ScraperConfig.model_json_schema()` → AI 프롬프트에 JSON Schema 제공
- 각 필드의 `description`이 AI에게 필드 용도 설명
- 검증 규칙이 AI 출력 오류를 사전 차단
- `field_validator`, `model_validator`가 논리적 일관성 검증

### 8-5. 플러그인 아키텍처 (Phase 4+)

config만으로 표현 불가능한 사이트별 커스텀 로직:

```python
class ScraperPlugin(Protocol):
    """사이트별 커스텀 변환 로직."""

    def transform_row(self, row: Tag, notice_data: dict) -> dict:
        """행 파싱 후 Notice 데이터 변환/보강."""
        ...

    def transform_link(self, raw_link: str) -> str:
        """링크 추출 후 URL 변환."""
        ...

    def post_process(self, notices: list[Notice]) -> list[Notice]:
        """전체 수집 완료 후 후처리."""
        ...
```

**등록:**

```python
# source_key → plugin 매핑
PLUGINS: dict[str, ScraperPlugin] = {
    "gntp": GntpPlugin(),
    "itp": ItpPlugin(),
}

# GenericScraper에서 자동 적용
plugin = PLUGINS.get(self.config.source_key)
if plugin:
    notice_data = plugin.transform_row(row, notice_data)
```

**활용 시나리오:**
- 비표준 날짜 형식 변환
- 동적 렌더링 페이지 전처리 (Playwright 연동)
- 로그인이 필요한 사이트 인증 처리
- 특수한 중복 제거 로직

---

## 9. 39개 프로덕션 config 목록

lets_portal에서 검증된 39개 사이트. 모든 config는 ScraperConfig 스키마와 호환.

| # | source_key | 기관명 | 특수 기능 |
|---|-----------|--------|-----------|
| 1 | alio | 신용보증기금 | |
| 2 | cba | 충청북도기업진흥원 | |
| 3 | cbist | 충청북도과학기술혁신원 | |
| 4 | ctp | 충남테크노파크 | parser: lxml |
| 5 | dicia | 대전정보문화산업진흥원 | 페이지네이션 없음 |
| 6 | dips | 대전기업정보포털 | |
| 7 | gbsa | 경기도경제과학진흥원 | |
| 8 | gcaf | 경남문화예술진흥원 | |
| 9 | gdtp | 경기대진테크노파크 | |
| 10 | gica | 강원정보문화산업진흥원 | |
| 11 | gicon | 광주정보문화산업진흥원 | |
| 12 | gjf | 경기도일자리재단 | |
| 13 | gnckl | 경남콘텐츠코리아랩 | |
| 14 | gnto | 경남관광재단 | |
| 15 | gntp | 경남테크노파크 | POST+JSON, grid_selector, JS링크 |
| 16 | gwto | 강원관광재단 | |
| 17 | ijto | 제주관광공사 | |
| 18 | itp | 인천테크노파크 | POST form, session_init, JS링크 |
| 19 | jbba | 전라북도경제통상진흥원 | |
| 20 | jcia | 전남정보문화산업진흥원 | |
| 21 | jcon | 전라북도콘텐츠융합진흥원 | |
| 22 | jica | 전주정보문화산업진흥원 | 페이지네이션 없음 |
| 23 | kiat | 한국산업기술진흥원 | |
| 24 | kcpi | 한국보육진흥원 | |
| 25 | keiti | 한국환경산업기술원 | |
| 26 | kidp | 한국디자인진흥원 | |
| 27 | kipa | 한국발명진흥회 | offset 기반 페이지네이션 |
| 28 | kised | 창업진흥원 | |
| 29 | kocca | 한국콘텐츠진흥원 | |
| 30 | koipa | 한국지식재산보호원 | JS링크, 페이지네이션 없음 |
| 31 | konkuk | 건국대학교 | JS링크 (3그룹), 페이지네이션 없음 |
| 32 | krihs | 국토연구원 | |
| 33 | nipa | 정보통신산업진흥원 | |
| 34 | ofjeju | 제주콘텐츠진흥원 | skip_no_date: false, 페이지네이션 없음 |
| 35 | ptp | 포항테크노파크 | |
| 36 | seoultp | 서울테크노파크 | |
| 37 | sjtp | 세종테크노파크 | |
| 38 | touraz | 한국관광공사 | |
| 39 | wku | 원광대학교 | |

---

## 10. 구현 순서 (Phase 2 작업용)

1. `ScraperConfig` Pydantic 모델 + 검증 규칙
2. `GenericScraper.__init__` (config 검증, BaseCollector 우회)
3. `_build_page_url` + `_fetch_page` (페이지네이션 엔진)
4. `_extract_link` (JS regex 포함 링크 추출)
5. `_make_bid_no` (해시 기반 bid_no 생성)
6. `_parse_rows` (HTML → Notice 변환)
7. `_fetch` (메인 수집 루프 조립)
8. `health_check` (1페이지 접근 테스트)
9. `__init__.py` export 추가 (`GenericScraper`, `ScraperConfig`)
10. 단위 테스트 작성
11. respx mock 통합 테스트 작성
12. 39개 프로덕션 config 검증 테스트
