# bid-collectors ↔ BidWatch 인터페이스 정의

> **이 문서는 양쪽 프로젝트에 동일하게 존재합니다.**
> 변경은 bid-collectors 쪽에서 하고 `docs/handover/v<버전>.md`로 넘긴다 — bidwatch 쪽 파일은 bidwatch 세션이 반영한다(2026-09-25).
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

    # === 출처 원문 전부 (v1.2.5) ===
    extra: dict | None = None
    # 응답 항목의 비어 있지 않은 필드 전부를 원래 이름 그대로 (CONTRACT.md 설계 원칙 '숨기지도 더하지도 않는다').
    # 표준 필드로 옮긴 값도 원문 그대로 다시 들어 있다. JSON은 값 타입·중첩 그대로, XML은 태그 → 텍스트
    # (같은 태그가 2개 이상이면 list, 자식이 있는 태그는 dict). 0·False는 값이고 None·빈 문자열은 뺀다.
    # 요청 문맥(용역/물품/공사, 낙찰/계약/사전규격)은 응답에 없으므로 extra가 아니라 bid_no 접두사에 있다.
    # 예(나라장터 용역): {"bidNtceNo": "R26BK01736671", "presmptPrce": "456714000", "bidMethdNm": "직찰", ...}
    # BidWatch는 이 필드를 JSONB로 저장. 키 이름은 출처 API 명세 그대로 — v1.2.4까지의 영어 별칭(est_price·contact 등)은 없다.
```

### 필드 규칙

| 규칙 | 설명 |
|------|------|
| `bid_no` 형식 | 수집기마다 자유. 단, source 내에서 UNIQUE 보장 |
| `bid_no` 예시 | 나라장터: `"용역-20260405001-00"`, 기업마당: `"BIZINFO-12345"`, 알리오: `"ALIO-3580351"`, LH: `"LH-2603329"`, 가스공사: `"KOGAS-2026092314"`, 국방전자조달: `"D2B-국내경쟁-2026ERA00055606N-3"`, 수자원공사: `"KWATER-B5202603396"`, 스크래퍼: `"SCR-kocca-a1b2c3d4e5"` |
| `status` 값 | `"ongoing"` (진행중), `"closed"` (마감), `"cancelled"` (취소). 기본값 `"ongoing"` |
| `budget` | 원 단위 정수. 미공개/미확인이면 `None` |
| `content` | HTML 태그 제거된 순수 텍스트. 공백/줄바꿈 정리 완료 상태 |
| `attachments` | `None`이면 첨부 없음. 빈 리스트 `[]`도 첨부 없음 |
| `extra` | 응답 항목 원문 전부, 원래 이름(v1.2.5). BidWatch는 JSONB로 통째 저장하고 화면에 보일 키는 BidWatch가 고른다 |

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
        공고 1건 상세 조회. 결과 캐싱은 소비자 몫. 수집(collect) 중에는 부르지 않는다 — 필요할 때 소비자가 1건씩 부른다.
        - K-Startup: content 전문 + 대상·신청방법 등 dict. 실패·없는 번호는 None(경고 로그).
        - 알리오(v1.3.0): `findBidDtl.json?seq=` → 아래 dict. **실패는 예외** — HTTP 오류(`httpx.HTTPStatusError` 등)·
          `status != "success"`·응답 형식 이상·`ALIO-{seq}` 형식이 아닌 bid_no는 `ValueError`.
          없는 seq도 알리오가 HTTP 200 + `status:"error"`("시스템 에러입니다. 관리자에게 문의하세요.")로 주므로 "없음"과 "장애"를 구분하지 않는다.
            {"attachments": [{"name": fileNm, "url": fileNo}, ...],  # 없으면 [] (None 아님)
             "content": str,                                          # bidDtl.content HTML 제거, 없으면 "" (실측상 늘 빈 값)
             ...data.bidDtl의 비어 있지 않은 필드 전부, 원래 이름}     # refrUrl·bidType·apbaId·ingStatus·totContAmt(0 포함)·bFiles …
        - 기관 수집기 4종(v1.5.0): 알리오와 같은 모양 — 원천의 비어 있지 않은 필드 전부(0 포함, 값은 원문 그대로) +
          `attachments: [{"name", "url"}]`(없으면 []) + `content`(넷 다 원천에 본문 필드가 없어 "" — 공고문은 첨부 hwp).
          **실패·없는 공고는 예외**(자기 접두사가 아닌 bid_no는 요청 없이 `ValueError`). 아래 표 참조.
        - 나머지(나라장터·기업마당·보조금24·중소벤처기업부·GenericScraper)는 None.
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

### 기관 수집기 `fetch_detail` (v1.5.0)

| 수집기 | 원천 · 호출 수 | 반환 키 | 없는 공고·고장 |
|---|---|---|---|
| `KwaterCollector` | 사이트 내부 JSON `POST ebid.kwater.or.kr/.../selectBidPblancDtl.do` · 1회 | `data.tndrPblanc` 필드(요청금액 `rqestAmt`·입찰방법·담당자 등)를 평탄화 + `data`의 나머지 필드 원문(입찰 일정 `tndrPrgsOrdrList`·`atchflList` 등). 첨부 name = `docFileNm` | `tndrPblanc` null(없는 번호도 `success`로 온다)·`code != success`·`atchflList` 형식 이상·첨부 이름/ID 없음·키 충돌 → `ValueError`, HTTP 오류 → `httpx.HTTPStatusError` |
| `D2bCollector` | 공식 API 상세 5종 · 국내·국외경쟁 1회, **시설경쟁·국내수의·시설수의 2회**(목록 조회 1 + 상세 1 — 상세 필수 값이 bid_no에 없다) | 상세 `item` 필드 원래 이름(`estmPrce`·`scsbidLwltRt`·`areaLmttList`·`lcnsLmttList`·`lc` …, `^` 구분 문자열도 원문). `attachments == []`(첨부 필드 없음) | 결과 0건(없는 공고와 파라미터 불일치를 d2b가 같은 빈 응답으로 준다)·목록에서 행을 못 찾음·`resultCode != 00` → `ValueError`, HTTP 오류 → `RuntimeError`(키 가림) |
| `KogasCollector` | 사이트 HTML `bid_detail_view_notice.jsp` · 1회 | **화면 항목명**(`추정가격`·`부가세`·`합계금액`·`계약방법`·`개찰일시`·`5. 업체제시문` …, 값은 공백 정리한 원문 — `()`·`~` 같은 빈 표기도 그대로) + `진행상태`·`진행안내`(취소는 안내 문구 "아래의 입찰이 취소되었습니다."에만) + `품목내역`(list[dict]). 첨부 = 페이지의 내려받기 링크 전부(공고 첨부 `bid_download_attfile` · 표준 계약조건 `bid_download_rule_proc` · 구매요청). 같은 항목명이 다시 나오면 값이 list(표본엔 없음) | "정보가 존재하지 않습니다"·공고번호 칸 불일치·건명 없음·첨부 절/첨부 표 링크 수 불일치 → `ValueError`, HTTP 오류(파라미터 오류 400 포함) → `httpx.HTTPStatusError` |
| `LhCollector` | 사이트 HTML — 검색 1회(최신 차수·업무 코드) + 상세 1회 = 2회 | **`"표 이름/항목명"`**(`공고일반정보/추정가격`·`입찰진행정보/입찰서접수마감일시`·`투찰제한정보/참가지역1` …, 같은 키가 다시 나오면 list — 표본엔 없음) + 목록형 표 `요구면허`·`요구면허#2`·`파일정보`·`공고변경정보`(list[dict]) | 검색 결과 없음·공고번호 칸 ≠ `{번호} - {차수}`·건명 없음·파일정보 표 없음·첨부 링크 일부만 읽힘 → `ValueError`, HTTP 오류 → `httpx.HTTPStatusError`, TLS 검증 실패 → `httpx.ConnectError` |

- **수자원·가스·LH는 공식 API가 아니라 기관 사이트 화면이다** — 사이트가 개편되면 깨지고, 그때는 빈 dict가 아니라 위 예외로 드러난다
  (핵심 칸 대조 — 공고번호·건명). 소비자는 경고 로그 후 다음에 다시 시도하면 된다.
- 한도: d2b는 오퍼레이션당 100회/일을 목록 수집(`collect`)과 **나눠 쓴다**(시설경쟁·수의 2종의 상세는 목록 오퍼레이션도 1회 쓴다). 사이트 3곳은 한도 없음(연속 20~46회 차단 없음, 실측).
- 첨부 url은 그대로 GET하면 파일이 온다(세션·로그인 불필요, 2026-09-26 기관별 확인). **LH 첨부는 1GB가 넘는 것도 있다**(현장설명서 zip 1.2GB 실례) — 받을 거면 스트리밍으로.
- LH(`ebid.lh.or.kr`)는 서버가 중간 인증서를 보내지 않아 패키지가 동봉한 중간 인증서로 검증한다(검증은 켜져 있다). 서버가 첨부를 직접 받을 때도 같은 문제가 있다(브라우저 링크는 문제없다) — 패키지 내부 `bid_collectors.lh.lh_ssl_context()`를 `verify=`로 쓸 수 있으나 **계약 밖**(공개 export 아님, 예고 없이 바뀔 수 있다).

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
| (v1.2.4) API 수집기 항목의 필수 필드(ID·제목) 없음·null·형식 이상 | 그 항목만 빼고 보존 | `"항목 파싱 예외로 N건 건너뜀 — 필수 필드 없음: pblancId 3건, ValidationError: organization 1건 (응답 형식 변경 의심)"` (사유별 한 줄) | True |
| (v1.2.4) GenericScraper 목록 행은 잡혔는데 추출 0건·기준일 이전 행 0건 (셀렉터가 낡음 — 사이트 개편 의심) | 그 페이지에서 멈춤 | `"페이지 N: 셀렉터 불일치 의심 — 목록 행 R개 중 추출 0건 (제목 없음 a행, 날짜 없음 b행, 파싱 예외 c행)"` | True |

- 목록 행이 0개인 페이지는 빈 게시판과 구분할 수 없어 "공고가 0건 (정상)"으로 본다(셀렉터 불일치로 보고하지 않음).

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
| `AlioCollector` | (없음) | 알리오 공공기관 입찰공고 — 공개 JSON, bid_no `ALIO-{seq}` (v1.2.0), `fetch_detail` 첨부·원문 링크 (v1.3.0) |
| `LhCollector` | `DATA_GO_KR_KEY` | LH 입찰공고(15159012) — source `"LH"`, bid_no `LH-{bidNum}`(정정·취소는 같은 bid_no로 갱신) (v1.4.0), `fetch_detail` (v1.5.0) |
| `KogasCollector` | `DATA_GO_KR_KEY` | 한국가스공사 입찰정보(15157366) — source `"가스공사"`, bid_no `KOGAS-{NOTICE_CODE}` (v1.4.0), `fetch_detail` (v1.5.0) |
| `D2bCollector` | `DATA_GO_KR_KEY` | 국방전자조달 목록 5종(15158416) — source `"국방전자조달"`, bid_no `D2B-{국내경쟁·국외경쟁·시설경쟁·국내수의·시설수의}-{키}-{차수}`. 수의 2종은 진행 중 전량(공고일 필터 없음, start_date None) (v1.4.0), `fetch_detail` (v1.5.0) |
| `KwaterCollector` | `DATA_GO_KR_KEY` | 한국수자원공사 입찰공고 4종(15101635) — source `"수자원공사"`, bid_no `KWATER-{tndrPbanno}` (v1.4.0), `fetch_detail` (v1.5.0) |
| `GenericScraper` | (없음) | config만 필요 |

- 기관 수집기 4종(v1.4.0) 공통: `budget`은 `None` — 추정가격·기초금액·설계가 등 금액은 `extra`에 원래 이름으로 있다(어느 것을 budget으로 볼지는 원칙 ② 결정 전).
  `organization`은 LH·가스·수자원이 기관 공식명(알리오 `pname`과 같은 이름), d2b는 발주기관 `ornt`. `category`는 출처의 업무 구분(시설공사·용역·물품 등).
  각 API는 data.go.kr 활용신청이 필요하고 한도는 오퍼레이션별이다(d2b만 100회/일).
(한전·발전사(전력데이터개방포털 키 필요)·코레일·중소벤처24는 미구현 — bid-collectors `docs/institution_sources.md`)

---

## 6. 버전 호환성

- 이 인터페이스는 bid-collectors `v1.5.0` 기준 (변경 결정 기록: bid-collectors `docs/CONTRACT.md`)
- Notice 모델에 필드 추가는 호환 (Optional 기본값)
- 필드 제거/이름 변경은 메이저 버전 업 필요
- BidWatch는 `extra` 필드로 새 데이터를 수용하므로, 수집기가 extra에 넣는 것은 자유
