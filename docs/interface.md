# bid-collectors ↔ BidWatch 인터페이스 정의

> **이 문서는 양쪽 프로젝트에 동일하게 존재합니다.**
> 변경 시 양쪽 모두 업데이트할 것.
>
> - `bid-collectors/docs/interface.md`
> - `bidwatch/docs/interface.md`

---

## 1. Notice 모델 — 수집기 출력 데이터

```python
from pydantic import BaseModel
from datetime import date

class Notice(BaseModel):
    """수집기가 반환하는 공고 1건의 표준 모델.
    BidWatch는 이 모델을 bid_notices 또는 scraped_notices에 저장한다."""

    # === 필수 필드 ===
    source: str              # 출처명 ("나라장터", "기업마당", "K-Startup", ...)
    bid_no: str              # 공고 고유번호 (source 내에서 UNIQUE)
    title: str               # 공고 제목
    organization: str        # 발주/시행 기관명

    # === 날짜/상태 ===
    start_date: date | None = None   # 공고일
    end_date: date | None = None     # 마감일
    status: str = "ongoing"          # "ongoing" | "closed" | "cancelled"

    # === URL ===
    url: str                         # 원문 URL (목록 페이지 또는 상세 페이지)
    detail_url: str = ""             # 상세 페이지 URL (url과 다를 경우)

    # === 내용 ===
    content: str = ""                # 공고 내용 요약 (HTML 제거된 텍스트)
    budget: int | None = None        # 예산/추정가격 (원 단위, None이면 미공개)
    region: str = ""                 # 지역
    category: str = ""               # 분류 (예: "용역 > 학술연구")

    # === 첨부파일 ===
    attachments: list[dict] | None = None
    # 형식: [{"name": "파일명.pdf", "url": "https://..."}, ...]

    # === 수집기별 추가 데이터 ===
    extra: dict | None = None
    # 표준 필드에 안 맞는 수집기별 데이터를 여기에 넣음
    # 예: {"est_price": 50000000, "bid_method": "제한경쟁", "contact": "홍길동 02-1234-5678"}
    # BidWatch는 이 필드를 JSONB로 저장
```

### 필드 규칙

| 규칙 | 설명 |
|------|------|
| `bid_no` 형식 | 수집기마다 자유. 단, source 내에서 UNIQUE 보장 |
| `bid_no` 예시 | 나라장터: `"용역-20260405001-00"`, 기업마당: `"BIZINFO-12345"`, 스크래퍼: `"SCR-kocca-a1b2c3d4e5"` |
| `status` 값 | `"ongoing"` (진행중), `"closed"` (마감), `"cancelled"` (취소). 기본값 `"ongoing"` |
| `budget` | 원 단위 정수. 미공개/미확인이면 `None` |
| `content` | HTML 태그 제거된 순수 텍스트. 공백/줄바꿈 정리 완료 상태 |
| `attachments` | `None`이면 첨부 없음. 빈 리스트 `[]`도 첨부 없음 |
| `extra` | 수집기가 자유롭게 사용. BidWatch는 JSONB로 통째 저장 |

---

## 2. BaseCollector — 수집기 인터페이스

```python
from abc import ABC, abstractmethod
import os

class BaseCollector(ABC):
    source_name: str  # 서브클래스에서 지정

    def __init__(self, api_key: str | None = None, **kwargs):
        """
        API 키 전달 방식: 생성자 주입 + 환경변수 fallback.
        - api_key가 주어지면 그대로 사용
        - None이면 환경변수에서 자동 로드 (수집기별 변수명)
        - 둘 다 없으면 ValueError
        """
        self.api_key = api_key or os.environ.get(self._env_key())
        if not self.api_key:
            raise ValueError(
                f"{self.source_name}: API 키가 필요합니다. "
                f"생성자에 api_key를 전달하거나 환경변수 {self._env_key()}를 설정하세요."
            )

    def _env_key(self) -> str:
        """환경변수명. 서브클래스에서 오버라이드 가능."""
        return "DATA_GO_KR_KEY"  # 대부분의 공공 API 기본값

    @abstractmethod
    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list["Notice"], int]:
        """
        공고 수집 — 서브클래스가 구현.

        Args:
            days: 최근 N일간 공고 수집 (기본 1일)
            **kwargs: 수집기별 추가 옵션

        Returns:
            (notices 리스트, 처리한 페이지 수)
        """
        ...

    async def collect(self, days: int = 1, **kwargs) -> "CollectResult":
        """
        공고 수집 메인 메서드 (템플릿 메서드).
        _fetch()를 호출하고 중복 제거 + CollectResult 래핑을 자동 처리.
        서브클래스는 이 메서드를 오버라이드하지 않고 _fetch()만 구현.
        """
        ...

    async def health_check(self) -> dict:
        """
        API 연결 상태 확인.

        Returns:
            {"status": "ok"|"error", "message": str, "response_time_ms": int}
        """
        ...
```

### 호출 방식: **async**

```python
# BidWatch Celery 워커에서 호출하는 방식
import asyncio
from bid_collectors import NaraCollector

async def run_nara():
    collector = NaraCollector(api_key="xxx")  # 또는 환경변수 fallback
    result = await collector.collect(days=1)
    return result

# Celery task에서
result = asyncio.run(run_nara())
```

---

## 3. CollectResult — 수집 결과 + 메타데이터

```python
from pydantic import BaseModel
from datetime import datetime

class CollectResult(BaseModel):
    """collect() 메서드의 반환 타입."""

    # === 수집 데이터 ===
    notices: list[Notice]        # 수집된 공고 리스트

    # === 메타데이터 ===
    source: str                  # 출처명
    collected_at: datetime       # 수집 시각
    duration_seconds: float      # 소요 시간 (초)
    total_fetched: int           # API에서 가져온 원시 건수
    total_after_dedup: int       # 중복 제거 후 건수 (= len(notices))
    pages_processed: int         # 처리한 페이지 수

    # === 에러 ===
    errors: list[str]            # 수집 중 발생한 에러 메시��� (빈 리스트면 성공)
    is_partial: bool = False     # True면 일부만 수집됨 (중간 실패)
```

### BidWatch가 CollectResult를 사용하는 방식

```python
result = await collector.collect(days=1)

if result.errors:
    log.warning(f"[{result.source}] 부분 실패: {result.errors}")

for notice in result.notices:
    # bid_notices 또는 scraped_notices에 UPSERT
    upsert_notice(notice)

# 수집 이력 기록
save_collection_log(
    source=result.source,
    count=result.total_after_dedup,
    duration=result.duration_seconds,
    errors=result.errors,
)
```

---

## 4. GenericScraper — 스크래퍼 엔진 인터페이스

```python
class GenericScraper(BaseCollector):
    source_name = "scraper"  # config의 name으로 오버라이드

    def __init__(self, config: dict, **kwargs):
        """
        API 키 불필요. config(scraper_config JSON)만 받음.

        Args:
            config: scraper_registry.scraper_config에서 가져온 JSON dict
                    스키마: dev_reference.md §6 참조
        """
        self.config = config
        self.source_name = config.get("name", "scraper")

    async def collect(self, days: int = 30, **kwargs) -> CollectResult:
        """config 기반으로 HTML 게시판 스크래핑."""
        ...
```

### BidWatch에서 호출

```python
from bid_collectors import GenericScraper

# scraper_registry에서 config 로드
scraper = GenericScraper(config=registry.scraper_config)
result = await scraper.collect(days=30)
```

---

## 5. 수집기별 환경변수

| 수집기 | 환경변수 | 비고 |
|--------|---------|------|
| `NaraCollector` | `DATA_GO_KR_KEY` | 조달청 |
| `BizinfoCollector` | `BIZINFO_API_KEY` | 기업마당 (별도 키) |
| `Subsidy24Collector` | `DATA_GO_KR_KEY` | 보조금24 |
| `KstartupCollector` | `DATA_GO_KR_KEY` | K-Startup |
| `LHCollector` | `DATA_GO_KR_KEY` | LH |
| `KepcoCollector` | `DATA_GO_KR_KEY` | 한국전력 |
| `KexpresswayCollector` | `DATA_GO_KR_KEY` | 도로공사 |
| `KwaterCollector` | `DATA_GO_KR_KEY` | 수자원공사 |
| `DefenseCollector` | `DATA_GO_KR_KEY` | 방위사업청 |
| `Smes24Collector` | `DATA_GO_KR_KEY` | 중소벤처24 |
| `GenericScraper` | (없음) | config만 필요 |

---

## 6. 버전 호환성

- 이 인터페이스는 bid-collectors `v1.x` 기준
- Notice 모델에 필드 추가는 호환 (Optional 기본값)
- 필드 제거/이름 변경은 메이저 버전 업 필요
- BidWatch는 `extra` 필드로 새 데이터를 수용하므로, 수집기가 extra에 넣는 것은 자유
