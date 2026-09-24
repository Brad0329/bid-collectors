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
| `bid_no` 예시 | 나라장터: `"용역-20260405001-00"`, 기업마당: `"BIZINFO-12345"`, 알리오: `"ALIO-3580351"`, 스크래퍼: `"SCR-kocca-a1b2c3d4e5"` |
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
    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list["Notice"], int] | tuple[list["Notice"], int, list[str]]:
        """
        공고 수집 — 서브클래스가 구현 (패키지 내부 계약 — BidWatch는 부르지 않는다).

        Args:
            days: 최근 N일간 공고 수집 (기본 1일)
            **kwargs: 수집기별 추가 옵션

        Returns:
            (notices 리스트, 처리한 페이지 수[, 부분 실패·절단 메시지])  — v1.1부터 3번째 요소
        """
        ...

    async def collect(self, days: int = 1, **kwargs) -> "CollectResult":
        """
        공고 수집 메인 메서드 (템플릿 메서드).
        _fetch()를 호출하고 중복 제거 + CollectResult 래핑을 자동 처리.
        서브클래스는 이 메서드를 오버라이드하지 않고 _fetch()만 구현.
        """
        ...

    async def fetch_detail(self, bid_no: str) -> dict | None:
        """
        공고 1건 상세 조회. K-Startup만 구현(content 전문 + 대상·신청방법 등), 나머지는 None.
        결과 캐싱은 소비자 몫.
        """
        ...

    async def health_check(self) -> dict:
        """
        API 연결 상태 확인. 예외를 던지지 않는다.

        Returns:
            {"status": "ok"|"error", "source": str, "message": str(오류 시, API 키는 가려짐), "response_time_ms": int}
        """
        ...
```

### 나라장터 확장 메서드 (`NaraCollector`)

`collect_awards` · `collect_contracts` · `collect_pre_specs(days, **kwargs) -> list[Notice]` — `CollectResult`가 아니다.
실패(API 에러 응답·재시도 소진)는 **예외로** 호출자에게 간다(v1.1부터 재시도 소진도 조용한 빈 결과 대신 `RuntimeError`, 메시지의 API 키는 가려짐).
bid_no 접두사 `낙찰-`·`계약-`·`사전규격-`.

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
    errors: list[str] = []       # 수집 중 발생한 에러·절단 메시지 (빈 리스트면 완전 성공)
    is_partial: bool = False     # True면 일부만 수집됨 — errors가 비어 있지 않으면 항상 True
```

### `errors` / `is_partial`의 의미 (v1.1.0부터 채워짐)

| 상황 | notices | errors | is_partial |
|---|---|---|---|
| 정상 완료 | 전부 | `[]` | False |
| 공고가 0건 (정상) | `[]` | `[]` | False |
| 1페이지부터 요청 실패 (사이트 장애) | `[]` | `["페이지 1 요청 실패: ..."]` | True |
| N페이지에서 실패 | 앞 페이지까지 보존 | 실패 원인 | True |
| 나라장터 한 서비스의 resultCode 에러(쿼터 초과 등) | 다른 서비스 결과 보존 | `"물품 ... 22 - LIMITED_NUMBER... — 물품 남은 기간 중단"` | True |
| `max_pages` 상한에서 멈춤 | 상한까지 | `"max_pages=N 상한 도달로 중단 — 전체 M건 중 ..."` (GenericScraper는 전체 건수를 모름) | True |
| GenericScraper 세션 초기화 실패·행 파싱 예외 | 수집된 것 | 원인 / `"행 파싱 예외로 N행 건너뜀"` | True |

- **"사이트 장애"와 "공고 없음"은 errors로 구분한다** — v1.0.x에서는 둘 다 `[]`/False였다.
- errors 문자열에서 API 키(`serviceKey`·`ServiceKey`·`crtfcKey` 값, 키 원문·URL 인코딩 형태)는 `***`로 가려진다 — 수집 이력 DB에 그대로 저장해도 된다.
- 메시지 문구는 계약이 아니다(사람이 읽는 용도). 분기는 `is_partial`·`len(errors)`로 한다.

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

    def __init__(
        self,
        config: ScraperConfig | dict,
        event_hooks: dict[str, list[Callable]] | None = None,   # v1.1.0 추가
        **kwargs,
    ):
        """
        API 키 불필요.

        Args:
            config: ScraperConfig 또는 dict — dict는 생성 즉시 ScraperConfig로 검증된다
                    (실패 시 pydantic.ValidationError). 스키마: `ScraperConfig.model_json_schema()`,
                    설명: docs/generic_scraper.md
            event_hooks: httpx event_hooks 형식 ({"request": [...], "response": [...]}).
                    session_init_url·목록 페이지·health_check·리다이렉트로 따라가는 요청 **전부**에 걸린다.
                    SSRF 방어 훅을 꽂는 자리 — 훅이 예외를 던지면 그 요청은 실패로 errors에 기록되고
                    (1페이지면 0건 + is_partial) 차단된 URL로는 요청이 나가지 않는다.
        """

    async def collect(self, days: int = 30, **kwargs) -> CollectResult:
        """config 기반으로 HTML 게시판 스크래핑. kwargs: max_pages(config 오버라이드), delay(페이지 간격 초)."""
        ...
```

- `ScraperConfig`는 공개 export다(`from bid_collectors import ScraperConfig`). BidWatch의 AI가 이 스키마대로 config를 만든다.
- `pagination`이 비어 있으면(POST는 `page_param_key`가 없으면) 1페이지만 요청한다 — v1.0.x는 같은 URL을 `max_pages`번 다시 받았다.

### BidWatch에서 호출

```python
from bid_collectors import GenericScraper
from app.services.url_guard import guard_request

scraper = GenericScraper(
    config=registry.scraper_config,
    event_hooks={"request": [guard_request]},   # 정기·시험 수집 모두 SSRF 방어
)
result = await scraper.collect(days=30)
```

`create_client(**kwargs)`(`bid_collectors.utils.http`)도 `event_hooks`를 httpx에 그대로 넘긴다.

---

## 5. 수집기별 환경변수

| 수집기 | 환경변수 | 비고 |
|--------|---------|------|
| `NaraCollector` | `DATA_GO_KR_KEY` | 조달청 |
| `BizinfoCollector` | `BIZINFO_API_KEY` | 기업마당 (별도 키) |
| `Subsidy24Collector` | `DATA_GO_KR_KEY` | 보조금24 |
| `KstartupCollector` | `DATA_GO_KR_KEY` | K-Startup |
| `SmesCollector` | `DATA_GO_KR_KEY` | 중소벤처기업부 |
| `AlioCollector` | (없음) | 알리오 공공기관 입찰공고 — 공개 JSON, bid_no `ALIO-{seq}` (v1.2.0) |
| `GenericScraper` | (없음) | config만 필요 |

(공기업 API 5종·중소벤처24는 미구현 — bid-collectors `work_log/plan.md` '이후 단계')

---

## 6. 버전 호환성

- 이 인터페이스는 bid-collectors `v1.2.0` 기준 (변경 결정 기록: bid-collectors `docs/CONTRACT.md`)
- Notice 모델에 필드 추가는 호환 (Optional 기본값)
- 필드 제거/이름 변경은 메이저 버전 업 필요
- BidWatch는 `extra` 필드로 새 데이터를 수용하므로, 수집기가 extra에 넣는 것은 자유
