# Phase 0: 프로젝트 셋업 - 작업 로그

> 작성일: 2026-04-06
> 상태: 완료
> 테스트: 68개 전체 통과

---

## 1. 프로젝트 초기화

### 1-1. 디렉토리 구조

```
bid-collectors/
├── bid_collectors/
│   ├── __init__.py          # 패키지 버전(0.1.0), 공개 API export
│   ├── base.py              # BaseCollector 추상 클래스
│   ├── models.py            # Notice, CollectResult (Pydantic v2)
│   └── utils/
│       ├── __init__.py
│       ├── dates.py         # 날짜 파서
│       ├── text.py          # HTML 텍스트 정리
│       ├── http.py          # 공통 httpx 클라이언트
│       └── status.py        # 공고 상태 판정
├── tests/
│   ├── __init__.py
│   ├── test_dates.py        # 19개 테스트
│   ├── test_text.py         # 17개 테스트
│   ├── test_status.py       # 9개 테스트
│   ├── test_http.py         # 7개 테스트
│   ├── test_models.py       # 8개 테스트
│   └── test_base.py         # 8개 테스트
├── docs/
│   ├── plan.md
│   ├── bid_collectors.md
│   ├── dev_reference.md
│   └── pre_ready.md
└── pyproject.toml
```

### 1-2. pyproject.toml 설정

- **빌드 시스템**: setuptools + wheel
- **Python 요구사항**: >= 3.11
- **핵심 의존성**:
  - `httpx>=0.27` -- 비동기 HTTP 클라이언트 (lets_portal의 requests를 대체)
  - `beautifulsoup4>=4.12` -- HTML 파싱
  - `pydantic>=2.0` -- 데이터 모델
  - `lxml>=5.0` -- XML 파싱 (나라장터 등)
  - `python-dotenv>=1.0` -- 환경변수 로딩
- **개발 의존성**: pytest>=8.0, pytest-asyncio>=0.23, respx>=0.21, ruff>=0.4
- **pytest 설정**: `asyncio_mode = "auto"`, testpaths = ["tests"]
- **ruff 설정**: target-version = "py311", line-length = 100

---

## 2. 핵심 모델 구현

### 2-1. Notice 모델 (`bid_collectors/models.py`)

Pydantic v2 BaseModel. 수집기가 반환하는 공고 1건의 표준 스키마.

**필수 필드:**
| 필드 | 타입 | 설명 |
|------|------|------|
| `source` | `str` | 수집 출처 (예: "나라장터") |
| `bid_no` | `str` | 공고 고유번호 (중복 제거 키) |
| `title` | `str` | 공고명 |
| `organization` | `str` | 발주기관 |
| `url` | `str` | 공고 URL |

**선택 필드 (기본값 있음):**
| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `start_date` | `date \| None` | `None` | 공고 시작일 |
| `end_date` | `date \| None` | `None` | 마감일 |
| `status` | `str` | `"ongoing"` | 상태 (ongoing/closed) |
| `detail_url` | `str` | `""` | 상세 URL |
| `content` | `str` | `""` | 공고 본문 |
| `budget` | `int \| None` | `None` | 예산 (원) |
| `region` | `str` | `""` | 지역 |
| `category` | `str` | `""` | 분류 |
| `attachments` | `list[dict] \| None` | `None` | 첨부파일 목록 `[{"name": ..., "url": ...}]` |
| `extra` | `dict \| None` | `None` | 수집기별 추가 데이터 (BidWatch에서 JSONB 저장용) |

**설계 포인트:**
- `start_date`, `end_date`는 문자열 "2024-03-01"을 넣으면 Pydantic이 자동으로 `date` 객체로 변환
- `extra`에 수집기별 고유 필드를 자유롭게 담을 수 있어 스키마 변경 없이 확장 가능

### 2-2. CollectResult 모델 (`bid_collectors/models.py`)

`collect()` 메서드의 반환 타입. 수집 결과 + 메타데이터를 함께 전달.

| 필드 | 타입 | 설명 |
|------|------|------|
| `notices` | `list[Notice]` | 수집된 공고 목록 |
| `source` | `str` | 수집 출처명 |
| `collected_at` | `datetime` | 수집 시각 |
| `duration_seconds` | `float` | 수집 소요 시간(초) |
| `total_fetched` | `int` | 원본 수집 건수 (중복 제거 전) |
| `total_after_dedup` | `int` | 중복 제거 후 건수 |
| `pages_processed` | `int` | 처리한 페이지 수 |
| `errors` | `list[str]` | 에러 메시지 목록 (기본 빈 리스트) |
| `is_partial` | `bool` | 부분 수집 여부 (기본 False) |

---

## 3. BaseCollector 추상 클래스 (`bid_collectors/base.py`)

모든 수집기의 부모 클래스. 핵심 로직은 `collect()` 메서드에 집중.

### 3-1. 클래스 구조

```
BaseCollector (ABC)
├── source_name: str               # 수집기 이름 (서브클래스에서 지정)
├── __init__(api_key=None)          # API 키: 인자 > 환경변수 > ValueError
├── _env_key() -> str               # 환경변수명 (기본 "DATA_GO_KR_KEY")
├── _fetch(days, **kwargs)          # [추상] 서브클래스가 구현하는 실제 수집 로직
├── collect(days, **kwargs)         # 메인 진입점: _fetch + 중복제거 + CollectResult 래핑
└── health_check() -> dict          # API 연결 상태 확인 (기본 구현: not implemented 반환)
```

### 3-2. collect() 메서드 핵심 흐름

```
1. 시작 시각 기록
2. try: _fetch(days, **kwargs) 호출 → notices 리스트 획득
   except: errors에 에러 메시지 기록, is_partial=True
3. (source, bid_no) 튜플 기준 중복 제거 (순서 보존)
4. CollectResult 생성하여 반환
   - total_fetched: 원본 건수
   - total_after_dedup: 중복 제거 후 건수
   - duration_seconds: round(소요시간, 2)
   - pages_processed: kwargs에서 _pages_processed 키로 전달 (기본 0)
```

### 3-3. API 키 처리 순서

1. 생성자 인자 `api_key`
2. 환경변수 `_env_key()` 반환값 (기본 `DATA_GO_KR_KEY`)
3. 둘 다 없으면 `ValueError` 발생

### 3-4. 서브클래스 구현 가이드 (Phase 1 이후)

```python
class NaraCollector(BaseCollector):
    source_name = "나라장터"

    def _env_key(self) -> str:
        return "DATA_GO_KR_KEY"  # 기본값과 같으면 오버라이드 불필요

    async def _fetch(self, days=1, **kwargs) -> list[Notice]:
        # 실제 API 호출 및 Notice 리스트 생성
        ...
```

---

## 4. 유틸리티 이식 (lets_portal에서 가져옴)

### 4-1. 날짜 파서 (`bid_collectors/utils/dates.py`)

**함수**: `parse_date(text: str) -> str | None`

입력 문자열에서 날짜를 추출하여 `yyyy-MM-dd` 형식으로 정규화. 매칭 실패 시 None 반환.

**지원 패턴 (우선순위 순):**

| 패턴 | 예시 | 결과 |
|------|------|------|
| 기간 (~ 구분) | `2024-03-28 ~ 2024-04-05` | `2024-03-28` (시작일) |
| yyyy-MM-dd | `2024-03-28`, `2024.03.28`, `2024/03/28` | `2024-03-28` |
| yyyyMMdd (8자리) | `20240328` | `2024-03-28` |
| yyyyMMddHHmm (12자리) | `202403281400` | `2024-03-28` |
| yy-MM-dd (2자리 연도) | `24-03-28` | `2024-03-28` |
| 한글 | `2024년 3월 28일` | `2024-03-28` |

**핵심 로직:**
- 정규식 패턴 리스트를 순회하며 첫 매칭 사용
- `datetime(y, mo, d)`로 유효성 검증 (존재하지 않는 날짜 거부: 2월 30일 등)
- 2자리 연도는 `2000 +` 처리
- 텍스트 중간에 날짜가 있어도 추출 가능 (search 사용)

### 4-2. HTML 텍스트 정리 (`bid_collectors/utils/text.py`)

**함수 2개:**

`clean_html(text) -> str`
- HTML 엔티티 디코딩 (`html.unescape`)
- `<br>` 태그 → 줄바꿈 (`\n`)
- 나머지 HTML 태그는 유지
- 앞뒤 공백 strip

`clean_html_to_text(html_str) -> str`
- HTML 엔티티 디코딩
- 블록 태그(`</p>`, `</div>`, `</li>`, `</tr>`, `<br>`) → 줄바꿈
- 나머지 태그 완전 제거 (`<[^>]+>`)
- 연속 공백 → 단일 공백
- 연속 줄바꿈 최대 2줄로 제한
- 각 줄 앞뒤 공백 strip

### 4-3. 상태 판정 (`bid_collectors/utils/status.py`)

**함수**: `determine_status(end_date_str, date_format="%Y-%m-%d") -> str`

- 마감일이 오늘 이후(포함) → `"ongoing"`
- 마감일이 어제 이전 → `"closed"`
- 마감일이 None/빈 문자열/파싱 불가 → `"ongoing"` (안전한 기본값)
- `date_format` 파라미터로 커스텀 형식 지원

### 4-4. HTTP 클라이언트 (`bid_collectors/utils/http.py`)

**함수**: `create_client(**kwargs) -> httpx.AsyncClient`

**기본 설정:**
| 항목 | 값 |
|------|-----|
| 타임아웃 | 15초 |
| User-Agent | Chrome 125 (Windows) |
| 재시도 | 3회 (`AsyncHTTPTransport(retries=3)`) |
| 리다이렉트 | 자동 추적 (`follow_redirects=True`) |

**사용법:**
```python
from bid_collectors.utils.http import create_client

async with create_client() as client:
    resp = await client.get("https://api.example.com/data")
```

- `headers`, `timeout` 등 kwargs로 기본값 오버라이드 가능
- 커스텀 User-Agent: `create_client(headers={"User-Agent": "MyBot/1.0"})`
- 커스텀 타임아웃: `create_client(timeout=30.0)`

---

## 5. 패키지 공개 API (`bid_collectors/__init__.py`)

```python
__version__ = "0.1.0"

from .models import Notice, CollectResult
from .base import BaseCollector

__all__ = ["Notice", "CollectResult", "BaseCollector"]
```

외부에서 사용 시:
```python
from bid_collectors import Notice, CollectResult, BaseCollector
```

---

## 6. 테스트 요약

총 68개 테스트, 전체 통과.

| 파일 | 테스트 수 | 대상 |
|------|-----------|------|
| `test_dates.py` | 19 | parse_date: 표준 형식 9개 + 기간 3개 + 엣지 7개 |
| `test_text.py` | 17 | clean_html 9개 + clean_html_to_text 8개 |
| `test_status.py` | 9 | ongoing/closed 판정 4개 + 엣지 5개 |
| `test_http.py` | 7 | create_client 반환값, 기본설정, 커스텀 설정 |
| `test_models.py` | 8 | Notice 필수/선택 필드 5개 + CollectResult 3개 |
| `test_base.py` | 8 | 초기화 3개 + 추상 1개 + collect() 4개 |

**테스트 주요 검증 사항:**
- Notice: 필수 필드 누락 시 ValidationError, date 문자열 자동 변환, 기본값
- CollectResult: errors 기본 빈 리스트, is_partial 기본 False
- BaseCollector: API 키 우선순위 (인자 > 환경변수 > ValueError), _fetch 미구현 시 TypeError, collect()의 중복 제거, _fetch 예외 시 is_partial=True + errors 기록
- parse_date: 6가지 패턴 정상 파싱, 잘못된 날짜 None, 텍스트 속 날짜 추출
- determine_status: 동적 날짜 (today +/- timedelta) 사용하여 시간 경과에 무관한 테스트

---

## 7. 주요 실수 및 향후 기억할 점

### 실수

1. **pyproject.toml build-backend 오류**: 처음에 `"setuptools.backends._legacy:_Backend"`로 잘못 지정하여 `pip install -e .` 실패. `"setuptools.build_meta"`로 수정하여 해결.

### 향후 기억할 점

1. **비동기 전환 완료**: lets_portal은 동기(requests) 기반이었으나, bid-collectors는 전면 비동기(httpx) 설계. 모든 수집기의 `_fetch`는 `async def`여야 함.
2. **저장 로직 제거**: lets_portal의 `collect_and_save()` 패턴을 버리고, 수집기는 데이터 fetch만 담당. DB 저장은 BidWatch 본체 책임.
3. **중복 제거 키**: `(source, bid_no)` 튜플. 동일 소스 내에서 bid_no가 같으면 중복으로 처리.
4. **_pages_processed 전달 방식**: collect()의 kwargs에서 `_pages_processed` 키로 전달. 서브클래스의 _fetch에서 직접 CollectResult에 접근하지 않음.
5. **기본 API 키 환경변수**: `DATA_GO_KR_KEY`. 대부분의 공공데이터포털 수집기가 이 키를 공유. 다른 출처는 `_env_key()` 오버라이드.
6. **pytest-asyncio 설정**: `asyncio_mode = "auto"` 필수. 이 설정 없으면 async 테스트마다 `@pytest.mark.asyncio` 데코레이터가 필요.

---

## 8. Phase 1 진입 조건 (체크리스트)

- [x] 프로젝트 디렉토리 구조 생성
- [x] pyproject.toml 작성 및 `pip install -e .` 동작 확인
- [x] Notice + CollectResult 모델 구현
- [x] BaseCollector 추상 클래스 구현
- [x] 유틸리티 4개 이식 (dates, text, status, http)
- [x] 테스트 68개 작성 및 전체 통과
- [x] 가상환경 설정 및 의존성 설치 완료

---

## 9. Phase 1 작업 예고

Phase 1에서는 MVP 수집기 3개를 구현한다:

1. **나라장터** (`nara.py`): 용역/물품/공사 3개 서비스, XML 파싱, 페이지네이션, 429 재시도
2. **기업마당** (`bizinfo.py`): 지원사업정보 API
3. **보조금24** (`subsidy24.py`): 공공서비스 혜택 정보 API

각 수집기는 BaseCollector를 상속하여 `_fetch()` 메서드만 구현하면 된다.
