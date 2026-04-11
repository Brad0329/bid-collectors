# bid-collectors

공공기관 입찰공고/지원사업 API 통합 수집 Python 패키지.

## 설치

```bash
pip install git+https://github.com/Brad0329/bid-collectors.git
```

개발 환경:

```bash
git clone https://github.com/Brad0329/bid-collectors.git
cd bid-collectors
pip install -e ".[dev]"
```

## 환경변수

`.env.example`을 `.env`로 복사 후 키 입력:

```env
DATA_GO_KR_KEY=       # 공공데이터포털 통합 인증키 (나라장터, 보조금24, K-Startup, 중소벤처기업부)
BIZINFO_API_KEY=      # 기업마당 전용 API 키
```

## 수집기 목록

| 수집기 | 클래스 | 출처 | API 키 |
|--------|--------|------|--------|
| 나라장터 | `NaraCollector` | 조달청 | `DATA_GO_KR_KEY` |
| 기업마당 | `BizinfoCollector` | bizinfo.go.kr | `BIZINFO_API_KEY` |
| 보조금24 | `Subsidy24Collector` | 공공데이터포털 | `DATA_GO_KR_KEY` |
| K-Startup | `KstartupCollector` | 공공데이터포털 | `DATA_GO_KR_KEY` |
| 중소벤처기업부 | `SmesCollector` | 공공데이터포털 | `DATA_GO_KR_KEY` |
| 범용 스크래퍼 | `GenericScraper` | config 기반 HTML | 불필요 |

## 사용법

### 기본 수집

```python
import asyncio
from bid_collectors import NaraCollector

collector = NaraCollector(api_key="YOUR_KEY")
result = asyncio.run(collector.collect(days=7))

print(f"수집: {len(result.notices)}건, {result.pages_processed}페이지")
for notice in result.notices[:3]:
    print(f"  [{notice.bid_no}] {notice.title}")
```

### 나라장터 확장 (낙찰/계약/사전규격)

```python
collector = NaraCollector(api_key="YOUR_KEY")

# 낙찰정보 — 낙찰자, 낙찰금액, 낙찰율
awards = asyncio.run(collector.collect_awards(days=1))

# 계약정보 — 계약명, 계약금액, 계약기간
contracts = asyncio.run(collector.collect_contracts(days=1))

# 사전규격 — 규격서 파일, 의견마감일, 배정예산
pre_specs = asyncio.run(collector.collect_pre_specs(days=1))
```

### 상세 조회 (fetch_detail)

```python
collector = NaraCollector(api_key="YOUR_KEY")

# 목록에서 가져온 bid_no로 상세 정보 조회
detail = asyncio.run(collector.fetch_detail("용역-R26BK01457928-000"))
if detail:
    print(detail["content"])  # 사업개요

# K-Startup도 동일 패턴
kstartup = KstartupCollector(api_key="YOUR_KEY")
detail = asyncio.run(kstartup.fetch_detail("KSTARTUP-177157"))
```

지원 현황:

| 수집기 | fetch_detail | 방식 |
|--------|-------------|------|
| NaraCollector | O | g2b.go.kr 상세 페이지 스크래핑 |
| KstartupCollector | O | API 단건 필터 조회 |
| 그 외 | X | None 반환 (미지원) |

### GenericScraper (config 기반 HTML 스크래핑)

```python
from bid_collectors import GenericScraper

config = {
    "name": "한국콘텐츠진흥원",
    "source_key": "kocca",
    "list_url": "https://www.kocca.kr/kocca/tender/list.do?menuNo=204106&cate=01",
    "list_selector": "table tbody tr",
    "title_selector": "td:nth-child(2) a",
    "date_selector": "td:nth-child(5)",
    "link_base": "https://www.kocca.kr",
    "pagination": "&pageIndex={page}",
    "max_pages": 3,
}

scraper = GenericScraper(config)
result = asyncio.run(scraper.collect(days=30))
```

### Health Check

```python
collector = NaraCollector(api_key="YOUR_KEY")
status = asyncio.run(collector.health_check())
print(status)  # {"status": "ok", "source": "나라장터", "response_time_ms": 234}
```

## 데이터 모델

### Notice

```python
class Notice(BaseModel):
    source: str          # 출처명 ("나라장터", "기업마당", ...)
    bid_no: str          # 공고 고유번호
    title: str           # 공고 제목
    organization: str    # 발주 기관명
    start_date: date | None
    end_date: date | None
    status: str          # "ongoing" | "closed"
    url: str             # 상세 페이지 URL
    detail_url: str
    content: str
    budget: int | None   # 예산 (원)
    region: str
    category: str
    attachments: list[dict] | None  # [{"name": "...", "url": "..."}, ...]
    extra: dict | None   # 수집기별 추가 데이터
```

### CollectResult

```python
class CollectResult(BaseModel):
    notices: list[Notice]
    source: str
    collected_at: datetime
    duration_seconds: float
    total_fetched: int
    total_after_dedup: int
    pages_processed: int
    errors: list[str]
    is_partial: bool
```

## 테스트

```bash
# 단위 테스트 (네트워크 불필요)
pytest tests/ -m "not integration"

# 통합 테스트 (실제 API 호출, 키 필요)
pytest tests/ -m integration
```

## 라이선스

MIT
