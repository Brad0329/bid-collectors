# BidWatch 개발 레퍼런스 — lets_portal 핵심 코드 구조

> **용도:** BidWatch 개발 시 lets_portal 원본 코드를 열지 않고도 참조할 수 있는 기술 레퍼런스.
> **원본 위치:** `C:\Users\user\Documents\lets_portal\backend\`

---

## 1. 프로젝트 구조 (lets_portal/backend)

```
backend/
├── main.py                     FastAPI 앱 + 라우터 등록 (95줄)
├── config.py                   환경변수 기반 설정
├── database.py                 SQLite 스키마 + 초기 데이터
├── routers/                    API 라우터 10개
│   ├── auth.py, users.py, notices.py, tags.py
│   ├── settings.py, keywords.py, sources.py
│   ├── collection.py, organizations.py
│   └── attachments.py
├── collectors/                 수집기
│   ├── base.py                 BaseCollector (공통 UPSERT)
│   ├── nara.py                 나라장터 API
│   ├── kstartup.py             K-Startup API
│   ├── mss_biz.py              중소벤처기업부 API
│   ├── ccei.py                 CCEI 지원사업 JSON API
│   ├── generic_scraper.py      범용 HTML 스크래퍼 (48개 사이트)
│   └── scraper_configs.json    38개 사이트 스크래핑 설정
└── utils/
    ├── db.py                   SQLite context manager
    ├── text.py                 HTML 정리 (clean_html, clean_html_to_text)
    ├── dates.py                날짜 파서 (format_date)
    ├── keywords.py             키워드 매칭/로딩
    └── status.py               공고 상태 판정 (ongoing/closed)
```

---

## 2. BaseCollector (`collectors/base.py`)

### 클래스 구조

```python
STANDARD_FIELDS = [
    "source", "title", "organization", "category",
    "bid_no", "start_date", "end_date", "status", "url", "keywords"
]

class BaseCollector:
    source_name: str  # 서브클래스에서 지정 ("나라장터", "K-Startup" 등)

    def collect_and_save(self, keywords=None, days=1, mode="daily") -> dict:
        """메인 오케스트레이터"""
        # 1. keywords가 None이면 DB에서 활성 키워드 로드
        # 2. fetch_announcements() 호출 (서브클래스 구현)
        # 3. post_filter() 적용 (훅, 기본은 패스스루)
        # 4. save_to_db() 실행
        # 반환: {"new": N, "updated": M, "total": T}

    def fetch_announcements(self, keywords, days=1) -> list[dict]:
        """추상 메서드 — 서브클래스가 구현"""
        raise NotImplementedError

    def post_filter(self, notices) -> list[dict]:
        """후처리 훅 (나라장터: 관심 중분류 필터 등)"""
        return notices

    def save_to_db(self, notices) -> tuple[int, int]:
        """범용 UPSERT — source+bid_no 기준"""
```

### save_to_db() UPSERT 로직

```python
def save_to_db(self, notices):
    for notice in notices:
        # 1. STANDARD_FIELDS에서 값 추출
        standard = {f: notice.get(f, "") for f in STANDARD_FIELDS}
        standard["source"] = self.source_name

        # 2. 나머지 키 → extra 필드로 동적 처리
        extra = {k: v for k, v in notice.items() if k not in STANDARD_FIELDS}
        # extra 예: detail_url, apply_url, content, budget, est_price, region, ...

        # 3. bid_no 기준 기존 데이터 확인
        existing = cursor.execute(
            "SELECT id FROM bid_notices WHERE source=? AND bid_no=?",
            (source, bid_no)
        )

        if existing:
            # UPDATE — 표준 + extra 필드 모두 갱신
            # keywords는 기존 + 신규 합집합으로 병합
        else:
            # INSERT — 표준 + extra 필드 모두 삽입

    return (new_count, updated_count)
```

**BidWatch 변경:** SQLite → PostgreSQL. extra 필드를 JSONB 컬럼으로 통합 가능.

---

## 3. 나라장터 수집기 (`collectors/nara.py`)

### API 정보

```
엔드포인트: https://apis.data.go.kr/1230000/ad/BidPublicInfoService
서비스 3개:
  - getBidPblancListInfoServcPPSSrch  (용역)
  - getBidPblancListInfoThngPPSSrch   (물품)
  - getBidPblancListInfoCnstwkPPSSrch (공사)

인증: DATA_GO_KR_KEY (data.go.kr 발급)
응답: XML
페이지: 100건/페이지
```

### 수집 로직

```python
class NaraCollector(BaseCollector):
    source_name = "나라장터"

    def fetch_announcements(self, keywords, days=30, bid_types=["용역"]):
        notices = []

        # 1. 날짜 범위를 7일 단위로 분할 (API 제한)
        date_ranges = split_date_range(days, chunk=7)

        # 2. 3중 루프
        for bid_type in bid_types:          # 용역/물품/공사
            for keyword in keywords:         # 키워드별 검색
                for start, end in date_ranges:  # 7일 단위
                    params = {
                        "ServiceKey": API_KEY,
                        "bidNtceNm": keyword,    # 공고명 키워드 검색
                        "inqryBgnDt": start,
                        "inqryEndDt": end,
                        "numOfRows": 100,
                        "pageNo": page,
                    }
                    # 페이지네이션 반복
                    # 429 에러 시 3회 재시도 (30초 대기)

        # 3. bid_no 기준 중복 제거, 키워드 누적 병합
        # dedup_key = f"{bid_type}-{fullBidNo}"

        # 4. 관심 중분류 필터 (DB nara_interest_categories 테이블)
        return notices
```

### 공고 데이터 필드 매핑

```python
notice = {
    "title": item["bidNtceNm"],
    "organization": item["ntceInsttNm"],     # 발주기관
    "bid_no": f"{bid_type}-{fullBidNo}",
    "start_date": format_date(item["bidNtceDt"]),
    "end_date": format_date(item["bidClseDt"]),
    "status": determine_status(end_date),
    "url": f"https://www.g2b.go.kr/.../{fullBidNo}",
    "keywords": matched_keywords,
    # extra 필드:
    "detail_url": url,
    "est_price": item.get("presmptPrce"),      # 추정가격
    "budget": item.get("asignBdgtAmt"),         # 배정예산
    "bid_method": item.get("bidMethdNm"),       # 입찰방식
    "contract_method": item.get("cntrctMthdNm"),# 계약방식
    "category": f"{대분류} > {중분류}",
    "contact": f"{담당자} {전화}",
    "attachments": [{"name": 파일명, "url": URL}, ...],  # 최대 10개
    "file_url": 첫번째_첨부_URL,
}
```

**BidWatch 변경:**
- **전체 수집 모드 필요:** 현재는 키워드별 호출 → 키워드 없이 당일 전체 공고 수집으로 변경
- 키워드 필터링을 수집 시점이 아닌 조회 시점(PostgreSQL FTS)으로 이동
- 관심 중분류 필터를 사용자별 설정으로 전환 (또는 제거)

---

## 4. K-Startup 수집기 (`collectors/kstartup.py`)

### API 정보

```
엔드포인트: https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01
인증: DATA_GO_KR_KEY
응답: JSON
페이지: 100건/페이지
```

### 수집 로직

```python
class KstartupCollector(BaseCollector):
    source_name = "K-Startup"

    def fetch_announcements(self, keywords, days=30, only_ongoing=True):
        # 1. 전체 공고를 페이지네이션으로 수집
        params = {
            "ServiceKey": API_KEY,
            "numOfRows": 100,
            "pageNo": page,
        }
        if only_ongoing:
            params["cond[rcrt_prgs_yn::EQ]"] = "Y"  # 모집진행중만

        # 2. 날짜 cutoff로 오래된 건 스킵
        # 3. title + content + target 합쳐서 키워드 매칭
        # 4. clean_html()로 HTML 정리
```

### 공고 데이터 필드 매핑

```python
notice = {
    "title": item["pbanc_nm"],
    "organization": "K-Startup",
    "bid_no": f"KSTARTUP-{item['pbanc_id']}",
    "start_date": format_date(item["rcpt_bgng_dt"]),
    "end_date": format_date(item["rcpt_end_dt"]),
    "url": f"https://www.k-startup.go.kr/web/contents/bizpbanc-detail.do?schM=view&pbancSn={id}",
    # extra:
    "detail_url": url,
    "content": clean_html(item.get("pbanc_ctnt", "")),
    "region": item.get("supt_regin_nm"),
    "target": item.get("biz_trget_nm"),
    "apply_url": item.get("rcpt_url"),
}
```

**BidWatch 변경:**
- only_ongoing 파라미터로 이미 전체 수집 모드 지원 가능
- display_settings 테이블 의존성 제거

---

## 5. 범용 스크래퍼 (`collectors/generic_scraper.py`) — 핵심

### scrape_site() — 설정 기반 스크래핑 엔진

```python
def scrape_site(config, days=30) -> list[dict]:
    """
    config(JSON)를 받아 임의의 게시판을 스크래핑하는 범용 엔진.
    BidWatch에서 AI가 생성한 config를 이 엔진에 넣으면 자동 수집.
    """

    # === 설정 추출 ===
    name = config["name"]
    source_key = config["source_key"]
    list_url = config["list_url"]
    list_selector = config["list_selector"]
    title_selector = config["title_selector"]
    date_selector = config["date_selector"]
    link_attr = config.get("link_attr", "href")
    link_base = config.get("link_base", "")
    pagination = config.get("pagination")
    encoding = config.get("encoding", "utf-8")
    max_pages = config.get("max_pages", 3)
    parser = config.get("parser", "html.parser")

    # === 선택적 설정 ===
    offset_size = config.get("offset_size")        # offset 기반 페이지네이션
    link_js_regex = config.get("link_js_regex")     # JS 링크 파싱
    link_template = config.get("link_template")     # JS 링크 URL 템플릿
    session_init_url = config.get("session_init_url")  # 쿠키 획득
    post_data = config.get("post_data")             # POST 요청 데이터
    post_json_flag = config.get("post_json", False) # JSON body 여부
    page_param_key = config.get("page_param_key")   # POST 페이지 파라미터 키
    grid_selector = config.get("grid_selector")     # 데이터 영역 셀렉터
    skip_no_date = config.get("skip_no_date", True) # 날짜 없는 항목 스킵

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 ..."

    # === 쿠키 초기화 ===
    if session_init_url:
        session.get(session_init_url, timeout=10)

    notices = []
    cutoff = datetime.now() - timedelta(days=days)

    # === 페이지 순회 ===
    for page in range(1, max_pages + 1):
        # URL 구성
        if post_data:
            data = dict(post_data)
            if page_param_key:
                data[page_param_key] = str(page)
            if post_json_flag:
                resp = session.post(list_url, json=data, timeout=15)
            else:
                resp = session.post(list_url, data=data, timeout=15)
        else:
            url = list_url
            if pagination and page > 1:
                if "{offset}" in pagination:
                    offset = (page - 1) * offset_size
                    url = list_url + pagination.replace("{offset}", str(offset))
                else:
                    url = list_url + pagination.replace("{page}", str(page))
            resp = session.get(url, timeout=15)

        resp.encoding = encoding
        soup = BeautifulSoup(resp.text, parser)

        # grid_selector로 데이터 영역 특정
        if grid_selector:
            container = soup.select_one(grid_selector)
            if not container:
                break
            rows = container.select(list_selector)
        else:
            rows = soup.select(list_selector)

        if not rows:
            break

        page_has_old = False

        # === 행(row) 순회 ===
        for row in rows:
            # 제목 추출
            title_el = row.select_one(title_selector)
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            # 날짜 추출
            date_el = row.select_one(date_selector)
            date_text = date_el.get_text(strip=True) if date_el else ""
            parsed_date = _parse_date(date_text)
            if not parsed_date and skip_no_date:
                continue  # 날짜 없는 항목 스킵

            # 날짜 cutoff 체크
            if parsed_date:
                try:
                    d = datetime.strptime(parsed_date, "%Y-%m-%d")
                    if d < cutoff:
                        page_has_old = True
                        continue
                except:
                    pass

            # 링크 추출
            link_el = title_el if title_el.name == "a" else title_el.find("a")
            raw_link = ""
            if link_el:
                raw_link = link_el.get(link_attr, "")

            # JS 링크 변환
            if link_js_regex and raw_link:
                match = re.search(link_js_regex, raw_link)
                if match and link_template:
                    raw_link = link_template.replace("{id}", match.group(1))

            # 절대 URL 변환
            detail_url = urljoin(link_base or list_url, raw_link)

            # bid_no 생성 (결정적 해시)
            hash_src = f"{title}{raw_link}"
            bid_no = f"SCR-{source_key}-{hashlib.md5(hash_src.encode()).hexdigest()[:10]}"

            notices.append({
                "source": name,
                "title": title,
                "organization": name,
                "bid_no": bid_no,
                "start_date": parsed_date or "",
                "end_date": "",
                "status": _get_status(parsed_date),
                "url": detail_url,
                "detail_url": detail_url,
                "keywords": "",
            })

        # 오래된 공고만 있으면 다음 페이지 불필요
        if page_has_old and not notices_this_page:
            break

    return notices
```

### 날짜 파서 (_parse_date)

```python
def _parse_date(text) -> str | None:
    """5가지 날짜 패턴 자동 감지"""
    patterns = [
        (r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', 'yyyy-MM-dd'),   # 2024-03-28, 2024.03.28
        (r'(\d{4})(\d{2})(\d{2})', 'yyyyMMdd'),                       # 20240328
        (r'(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', 'yy-MM-dd'),     # 24-03-28
        (r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', '한국어'),          # 2024년 3월 28일
        (r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*~\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', '기간'),
    ]
    # 기간 형식이면 시작일 반환
    # 매칭되면 "yyyy-MM-dd" 형식으로 정규화하여 반환
    # 매칭 안 되면 None
```

---

## 6. scraper_configs.json — 전체 필드 스키마

### AI가 생성해야 할 JSON 구조

```json
{
  "name": "기관명 (필수)",
  "source_key": "짧은영문키 (필수, bid_no 생성용)",
  "list_url": "게시판 목록 URL (필수)",
  "list_selector": "행(row) CSS 셀렉터 (필수)",
  "title_selector": "제목 요소 CSS 셀렉터 (필수)",
  "date_selector": "날짜 요소 CSS 셀렉터 (필수)",

  "link_attr": "URL 속성 (기본: href)",
  "link_base": "상대 URL 변환용 base URL",
  "pagination": "페이지네이션 패턴 (?page={page} 또는 &offset={offset})",
  "max_pages": "최대 페이지 수 (기본: 3)",
  "encoding": "응답 인코딩 (기본: utf-8)",
  "parser": "BeautifulSoup 파서 (기본: html.parser)",

  "offset_size": "offset 기반 페이지네이션 시 한 페이지 건수",
  "link_js_regex": "JavaScript 함수에서 ID 추출 정규식",
  "link_template": "추출된 ID로 URL 생성 템플릿 ({id} 치환)",
  "session_init_url": "쿠키 획득용 초기 요청 URL",
  "post_data": "POST 요청 시 form/JSON 데이터 (dict)",
  "post_json": "true면 JSON body, false면 form data",
  "page_param_key": "POST data 내 페이지 번호 키",
  "grid_selector": "응답 HTML 내 데이터 영역 CSS 셀렉터",
  "skip_no_date": "날짜 없는 항목 스킵 여부 (기본: true)"
}
```

### 사이트 유형별 config 예시

#### 기본형 — GET + 테이블 (가장 흔한 패턴)

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
  "max_pages": 3
}
```

#### POST 기반 (form data)

```json
{
  "name": "인천테크노파크",
  "source_key": "itp",
  "list_url": "https://www.itp.or.kr/intro.asp",
  "list_selector": "table.board_list tbody tr",
  "title_selector": "td.subject a",
  "date_selector": "td:nth-child(4)",
  "session_init_url": "https://www.itp.or.kr/",
  "post_data": {"search": "1", "tmid": "14"},
  "page_param_key": "PageNum",
  "link_js_regex": "fncShow\\('(\\d+)'\\)",
  "link_template": "/intro.asp?tmid=14&seq={id}"
}
```

#### POST + JSON body

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
  "link_template": "http://account.more.co.kr/contract/orderalim_view.php?idx={id}"
}
```

#### 비테이블 레이아웃 (div/ul 기반)

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
  "skip_no_date": false
}
```

---

## 7. 유틸리티 함수

### text.py

```python
def clean_html(text: str) -> str:
    """HTML 엔티티 디코딩, <br> → 줄바꿈. 태그 제거 안 함 (단순)"""

def clean_html_to_text(html_str: str) -> str:
    """완전한 태그 제거, </p> → 줄바꿈, 공백 정리"""
```

### dates.py

```python
def format_date(dt_str: str) -> str:
    """4가지 패턴 지원: yyyy-MM-dd, yyyy.MM.dd, yyyyMMdd, yyyyMMddHHmm
    → 'yyyy-MM-dd' 형식으로 정규화"""
```

### keywords.py

```python
def match_keywords(search_text: str, keywords: list[str]) -> list[str]:
    """단순 substring 매칭. search_text에 포함된 키워드 리스트 반환"""

def load_active_keywords(source_id=None) -> list[str]:
    """DB에서 활성 키워드 로드. source_id=None이면 공통 키워드,
    source_id 지정하면 공통 + 해당 출처 전용 키워드 합산"""
```

### status.py

```python
def determine_status(end_date_str: str, date_format="%Y-%m-%d") -> str:
    """마감일 기준 상태 판정: 'ongoing' 또는 'closed'"""
```

### db.py

```python
@contextmanager
def db_cursor(commit=False):
    """SQLite cursor context manager. commit=True면 자동 커밋"""

@contextmanager
def db_connection():
    """SQLite conn+cursor context manager"""
```

---

## 8. 전용 수집기 (JSON API 사이트)

generic_scraper.py에 하드코딩된 전용 수집기 3개. config 기반이 아닌 직접 구현.

### CCEI 입찰공고 (`scrape_ccei_allim`)

```python
def scrape_ccei_allim(region_code, region_name, days):
    """CCEI 7개 지역센터 입찰공고 (allimList.json)"""
    # POST https://ccei.creativekorea.or.kr/{region}/allim/allimList.json
    # JSON body: {"div_code": "2", "pageIndex": page, ...}
    # 응답: JSON → 공고 목록 추출
    # 7개 지역: gyeonggi, gyeongnam, daegu, busan, sejong, incheon, chungbuk
```

### 부산창업포탈 (`scrape_busan_startup`)

```python
def scrape_busan_startup(days):
    """부산창업포탈 JSON API"""
    # POST https://busanstartup.kr/bizSup/list
    # JSON body: {"mcode": "biz02", "pageIndex": page, ...}
```

### 한국예탁결제원 (`scrape_ksd`)

```python
def scrape_ksd(days):
    """한국예탁결제원 JSON API"""
    # GET https://www.ksd.or.kr/api/bid-notice/list?page={page}&size=10
    # JSON 응답 직접 파싱
```

**BidWatch에서:** 이 3개는 사용자가 URL을 등록할 때 JSON API를 자동 감지하는 기능으로 대체하거나, "JSON API 모드" config 필드를 추가하여 범용화.

---

## 9. bid_notices 테이블 (lets_portal 현재 스키마)

```sql
CREATE TABLE bid_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,           -- 출처명 ("나라장터", "K-Startup", ...)
    title TEXT NOT NULL,
    organization TEXT DEFAULT '',   -- 발주기관
    category TEXT DEFAULT '',       -- 분류
    bid_no TEXT NOT NULL,           -- 공고번호 (source별 고유)
    start_date TEXT DEFAULT '',     -- 공고일 (yyyy-MM-dd)
    end_date TEXT DEFAULT '',       -- 마감일
    status TEXT DEFAULT 'ongoing',
    url TEXT DEFAULT '',            -- 원문 URL
    keywords TEXT DEFAULT '',       -- 매칭된 키워드 (콤마 구분)

    -- extra 필드 (수집기마다 다르게 사용)
    detail_url TEXT DEFAULT '',
    apply_url TEXT DEFAULT '',
    file_url TEXT DEFAULT '',
    content TEXT DEFAULT '',
    budget TEXT DEFAULT '',
    contact TEXT DEFAULT '',
    est_price TEXT DEFAULT '',
    region TEXT DEFAULT '',
    target TEXT DEFAULT '',
    attachments TEXT DEFAULT '',    -- JSON 문자열 [{name, url}, ...]

    -- AI 관련 (Phase C용)
    ai_status TEXT,
    ai_summary TEXT,
    ai_analyzed_at TEXT,
    attachment_dir TEXT,

    collected_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(source, bid_no)
);
```

---

## 10. BidWatch 이식 시 핵심 변경 사항 요약

### DB 레이어
- SQLite → PostgreSQL (SQLAlchemy async)
- `database.get_connection()` → PostgreSQL 세션
- 직접 SQL → SQLAlchemy ORM 또는 raw async SQL
- extra 필드들 → JSONB 컬럼 또는 명시적 컬럼

### 수집기 공통
- `BaseCollector.save_to_db()` + `generic_scraper.save_to_db()` 중복 → 하나로 통합
- tenant_id 파라미터 추가 (테넌트 URL 수집 결과는 tenant_notices로)
- 키워드 매칭을 수집 시점 → 조회 시점으로 이동 (공공 API 수집)

### 유틸리티 통합
- `dates.format_date()` + `generic_scraper._parse_date()` → 하나로 통합 (`_parse_date`가 더 강력)
- `status.determine_status()` + `generic_scraper._get_status()` → 하나로 통합
- `text.py`, `keywords.match_keywords()` → 그대로 재사용

### 나라장터 수집기
- 키워드별 호출 → 키워드 없이 당일 전체 수집
- 관심 중분류 필터 → 사용자별 설정 또는 제거

### generic_scraper.py (AI 스크래퍼 엔진)
- `scrape_site()` 함수 → 그대로 포팅 (핵심 엔진)
- `scraper_configs.json` → AI가 동일 포맷으로 자동 생성
- CCEI/부산/KSD 전용 수집기 → JSON API 모드 config으로 범용화 검토
- `save_to_db()` → BaseCollector 통합 버전 사용
- `collect_all_scrapers()` → 테넌트별 수집 스케줄러로 재설계
