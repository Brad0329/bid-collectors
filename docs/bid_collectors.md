# bid-collectors — 공공 API 수집기 패키지

> **별도 프로젝트로 개발**, BidWatch 본체에서 `pip install` 또는 로컬 경로로 import

---

## 1. 목적

공공기관 입찰공고/지원사업 API를 통합 수집하는 Python 패키지.
BidWatch 외에도 재사용 가능한 독립 라이브러리로 설계.

---

## 2. 프로젝트 구조

```
bid-collectors/
├── bid_collectors/
│   ├── __init__.py
│   ├── base.py              BaseCollector (공통 인터페이스)
│   ├── models.py            Notice 데이터 모델 (Pydantic)
│   ├── nara.py              나라장터 (조달청)
│   ├── bizinfo.py           기업마당 (중기부)
│   ├── subsidy24.py         보조금24 (행안부)
│   ├── kstartup.py          K-Startup (창업진흥원)
│   ├── smes24.py            중소벤처24 (중소기업기술정보진흥원)
│   ├── defense.py           방위사업청
│   ├── lh.py                한국토지주택공사
│   ├── kepco.py             한국전력공사
│   ├── kexpressway.py       한국도로공사
│   ├── kwater.py            한국수자원공사
│   └── generic_scraper.py   AI 스크래퍼 엔진 (config 기반)
├── tests/
├── pyproject.toml
└── README.md
```

---

## 3. 공통 인터페이스

```python
from dataclasses import dataclass
from datetime import date

@dataclass
class Notice:
    """수집기 공통 출력 모델"""
    source: str              # 출처명 ("나라장터", "기업마당", ...)
    bid_no: str              # 공고 고유번호 (source별 UNIQUE)
    title: str
    organization: str        # 발주/시행 기관
    start_date: date | None  # 공고일
    end_date: date | None    # 마감일
    status: str              # ongoing / closed
    url: str                 # 원문 URL
    detail_url: str = ""
    content: str = ""
    budget: int | None = None
    region: str = ""
    category: str = ""
    attachments: list = None # [{name, url}, ...]
    extra: dict = None       # 수집기별 추가 필드

class BaseCollector:
    source_name: str

    def collect(self, days: int = 1, **kwargs) -> list[Notice]:
        """공고 수집 → Notice 리스트 반환"""
        raise NotImplementedError

    def health_check(self) -> dict:
        """API 연결 상태 확인"""
        raise NotImplementedError
```

BidWatch 본체는 `Notice` 리스트를 받아서 `bid_notices` 또는 `scraped_notices`에 저장.
저장 로직은 본체 책임, 수집기는 데이터 fetch만 담당.

---

## 4. 공공 API 목록 + 연동 단계

### 1단계 — MVP (필수, bid-collectors 초기 릴리스)

| 수집기 | 제공기관 | API | 내용 | 비고 |
|--------|---------|-----|------|------|
| `nara.py` | 조달청 | 입찰공고정보서비스 | 물품/용역/공사/외자 입찰공고 | 공공조달 80%+ 커버 |
| `bizinfo.py` | 중기부 | 기업마당 지원사업정보 | 중앙부처/지자체/유관기관 지원사업 | 기업 대상 지원사업 통합 |
| `subsidy24.py` | 행안부 | 보조금24 공공서비스 정보 | 전 부처/지자체 보조금/혜택 | 범위 가장 넓음 |

### 2단계 — 확장 (차별화)

| 수집기 | 제공기관 | API | 내용 | 비고 |
|--------|---------|-----|------|------|
| `lh.py` | LH | 입찰공고/계약/발주계획/사전규격/개찰 (5개) | 자체 전자조달 | 나라장터 미포함건 |
| `kepco.py` | 한국전력 | 전자입찰계약정보 | 자체 전자조달 | |
| `kexpressway.py` | 도로공사 | 계약공개현황 | 자체 전자조달 | |
| `kwater.py` | 수자원공사 | 입찰공고 | 자체 전자조달 | |
| `defense.py` | 방위사업청 | 입찰공고/결과/계획/계약 (5개) | 군수품 조달 | 방산 니치 |

### 3단계 — 부가가치

| 수집기 | 제공기관 | API | 내용 | 비고 |
|--------|---------|-----|------|------|
| `kstartup.py` | 창업진흥원 | K-Startup 조회서비스 | 창업지원 사업공고 | lets_portal에서 이식 |
| `smes24.py` | 중소기업기술정보진흥원 | 중소벤처24 공고정보 | 중기부 산하 통합공고 | |
| `nara.py` 확장 | 조달청 | 낙찰/계약/사전규격/통합 (4개 추가) | 입찰 전후 프로세스 | |

### 참고: 나라장터 세부 API (7개)

| API | 내용 | 단계 |
|-----|------|:----:|
| 입찰공고정보서비스 | 입찰공고 목록/상세 | 1단계 |
| 낙찰정보서비스 | 개찰결과, 낙찰자 | 3단계 |
| 계약정보서비스 | 계약목록/상세 | 3단계 |
| 사전규격정보서비스 | 사전규격 공개 | 3단계 |
| 계약과정통합공개서비스 | 전 과정 통합 조회 | 3단계 |
| 공공데이터개방표준서비스 | 행안부 표준 포맷 | 필요 시 |
| 사용자정보서비스 | 업체/기관 정보 | 필요 시 |

---

## 5. 공통 사항

- 모든 API: REST + XML/JSON, **무료** (공공데이터법)
- data.go.kr 활용신청 필요 (대부분 즉시/자동 승인)
- 개발계정 일 1,000건 (나라장터 일부 100건), 운영 전환 시 증량 가능
- API key는 환경변수로 주입 (`DATA_GO_KR_KEY` 등)

---

## 6. generic_scraper.py

lets_portal의 범용 스크래퍼 엔진을 이 패키지에 포함.
AI가 생성한 `scraper_config` JSON을 받아 임의의 게시판을 파싱.

- config 스키마: [dev_reference.md](dev_reference.md) §6 참조
- BidWatch에서는 `scraper_registry.scraper_config`에 저장된 설정으로 호출
- 출력은 동일한 `Notice` 모델
