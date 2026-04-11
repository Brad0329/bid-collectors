# Phase 2: GenericScraper 엔진 구현 - 작업 로그

> 작성일: 2026-04-11
> 상태: 완료
> 테스트: 단위 63개 전체 통과 (기존 237개 포함 총 300개 전체 통과)

---

## 1. 구현 범위

Phase 1의 API 기반 수집기와 달리, HTML 스크래핑 기반의 범용 수집 엔진을 구현했다. 설정(config)만으로 다양한 공고 사이트를 수집할 수 있는 구조.

| 구성 요소 | 파일 | 설명 |
|-----------|------|------|
| ScraperConfig | `generic_scraper.py` | Pydantic 설정 모델 |
| GenericScraper | `generic_scraper.py` | BaseCollector 상속 HTML 스크래핑 엔진 |

### 1-1. 디렉토리 변경사항

```
bid_collectors/
├── __init__.py              # GenericScraper, ScraperConfig 추가 export
└── generic_scraper.py       # ScraperConfig + GenericScraper (신규)

tests/
└── test_generic_scraper.py  # 63개 테스트 (신규)
```

### 1-2. 패키지 공개 API 변경 (`__init__.py`)

`GenericScraper`와 `ScraperConfig`를 `__all__`에 추가.

---

## 2. ScraperConfig 설정 모델

### 2-1. 필수 필드 (6개)

| 필드 | 타입 | 설명 |
|------|------|------|
| `name` | str | 수집기 표시 이름 |
| `source_key` | str | 소스 식별자 (`^[a-z0-9_]+$` 패턴) |
| `list_url` | str | 목록 페이지 URL |
| `list_selector` | str | 게시글 행 CSS 선택자 |
| `title_selector` | str | 제목 CSS 선택자 |
| `date_selector` | str | 날짜 CSS 선택자 |

### 2-2. 선택 필드

| 필드 | 기본값 | 설명 |
|------|--------|------|
| `link_attr` | `"href"` | 링크 추출 대상 속성 |
| `link_base` | `None` | 상대 URL 변환 시 기준 URL |
| `pagination` | `None` | 페이지네이션 URL 접미사 (예: `"&page={page}"`) |
| `max_pages` | `10` | 최대 페이지 수 |
| `encoding` | `None` | 응답 인코딩 (None이면 자동) |
| `parser` | `"html.parser"` | BeautifulSoup 파서 |
| `offset_size` | `None` | offset 페이지네이션 단위 |
| `link_js_regex` | `None` | JS 링크 정규식 (onclick 등) |
| `link_template` | `None` | JS 링크 템플릿 URL |
| `session_init_url` | `None` | 쿠키 초기화용 사전 요청 URL |
| `post_data` | `None` | POST 요청 폼 데이터 |
| `post_json` | `None` | POST 요청 JSON 데이터 |
| `page_param_key` | `None` | POST 페이지네이션 파라미터 키 |
| `grid_selector` | `None` | 그리드 레이아웃 선택자 |
| `skip_no_date` | `False` | 날짜 없는 행 건너뛰기 여부 |
| `verify_ssl` | `True` | SSL 인증서 검증 여부 |

### 2-3. Pydantic Validators (3개)

1. **validate_js_link_pair**: `link_js_regex`와 `link_template`는 반드시 함께 설정해야 함. 하나만 설정하면 `ValidationError`.
2. **validate_post_pagination**: `post_data` 또는 `post_json` 사용 시 `page_param_key` 필수.
3. **validate_offset_pagination**: `{offset}` 플레이스홀더 사용 시 `offset_size` 필수.

### 2-4. source_key 패턴 제약

`^[a-z0-9_]+$` — ASCII 소문자, 숫자, 언더스코어만 허용. bid_no 생성에 사용되므로 일관성 유지를 위해 한글/대문자 불가.

---

## 3. GenericScraper 엔진

### 3-1. BaseCollector 상속 구조

```python
class GenericScraper(BaseCollector):
    def __init__(self, config):
        # api_key=None으로 BaseCollector.__init__ 호출 — API 키 불필요
        # config가 dict이면 ScraperConfig로 자동 변환
```

BaseCollector는 api_key를 필수로 받는 구조이지만, GenericScraper는 HTML 스크래핑이므로 api_key가 불필요. `__init__` 오버라이드로 api_key=None을 전달하여 요구사항을 우회.

### 3-2. _fetch 메인 루프

```
async def _fetch(days, **kwargs) -> tuple[list[Notice], int]:
    cutoff = datetime.now() - timedelta(days=days)
    client = create_client(verify=config.verify_ssl)
    
    if session_init_url:
        client.get(session_init_url)  # 쿠키 초기화
    
    for page in range(1, max_pages + 1):
        html = _fetch_page(client, page)
        notices, should_stop = _parse_rows(html, cutoff)
        results.extend(notices)
        if should_stop: break
        await asyncio.sleep(0.5)  # 기본 딜레이
    
    return (results, pages_processed)
```

### 3-3. _fetch_page — 단일 페이지 HTTP 요청

POST와 GET 두 가지 방식 지원:

```python
if config.post_data or config.post_json:
    # POST 방식: page_param_key로 페이지 번호 설정
    data = config.post_data.copy()
    data[config.page_param_key] = str(page)
    resp = client.post(url, data=data)
else:
    # GET 방식: URL 접미사로 페이지네이션
    url = _build_page_url(page)
    resp = client.get(url)
```

### 3-4. _build_page_url — URL 생성

4가지 페이지네이션 패턴 지원:

| 패턴 | 예시 | 사용 사이트 수 |
|------|------|---------------|
| URL 접미사 + `{page}` | `&page={page}` | 32개 (lets_portal 대부분) |
| URL 접미사 + `{offset}` | `&start={offset}` | 1개 (kipa) |
| POST + `page_param_key` | form data로 페이지 전달 | 2개 (itp, gntp) |
| 페이지네이션 없음 | list_url 그대로 사용 | 7개 |

```python
def _build_page_url(self, page: int) -> str:
    if not self.config.pagination:
        return self.config.list_url
    suffix = self.config.pagination
    if "{offset}" in suffix:
        offset = (page - 1) * self.config.offset_size
        suffix = suffix.replace("{offset}", str(offset))
    else:
        suffix = suffix.replace("{page}", str(page))
    return self.config.list_url + suffix
```

### 3-5. _parse_rows — HTML 파싱

```python
def _parse_rows(self, html: str, cutoff: datetime) -> tuple[list[Notice], bool]:
    soup = BeautifulSoup(html, self.config.parser)
    rows = soup.select(self.config.list_selector)
    
    for row in rows:
        try:
            title_el = row.select_one(self.config.title_selector)
            date_el = row.select_one(self.config.date_selector)
            # 날짜 파싱 → cutoff 비교 → Notice 생성
        except Exception:
            logger.debug("row parse error")  # 행 단위 에러 격리
            continue
```

cutoff 이전 날짜가 발견되면 `should_stop=True` 반환하여 다음 페이지 요청 중단.

### 3-6. _extract_link — 링크 추출

3가지 링크 추출 방식:

1. **일반 href**: `title_el.get("href")` 또는 상위 `<a>` 태그의 href
2. **JS 정규식 (단일 캡처)**: `link_js_regex`로 onclick 등에서 ID 추출 → `link_template`에 삽입
3. **JS 정규식 (복수 캡처)**: 캡처 그룹이 2개 이상이면 `{0}`, `{1}` 플레이스홀더에 순서대로 삽입

```python
def _extract_link(self, title_el) -> str:
    if self.config.link_js_regex:
        raw = title_el.get("onclick", "") or str(title_el)
        m = re.search(self.config.link_js_regex, raw)
        if m:
            groups = m.groups()
            if len(groups) >= 2:
                link = self.config.link_template
                for i, g in enumerate(groups):
                    link = link.replace(f"{{{i}}}", g)
            else:
                link = self.config.link_template.replace("{}", groups[0])
            return link
    
    # 일반 href 추출
    href = title_el.get(self.config.link_attr, "")
    if href and self.config.link_base:
        href = urljoin(self.config.link_base, href)  # 상대→절대 변환
    return href
```

### 3-7. _make_bid_no — 결정론적 ID 생성

```python
def _make_bid_no(self, title: str, link: str) -> str:
    raw = f"{title}|{link}"
    hash_val = hashlib.md5(raw.encode()).hexdigest()[:10]
    return f"SCR-{self.config.source_key}-{hash_val}"
```

`SCR-{source_key}-{md5[:10]}` 형식. title과 link의 조합으로 결정론적 해시 생성 — 동일 공고는 항상 같은 bid_no.

### 3-8. health_check

```python
async def health_check(self) -> dict:
    # config의 max_pages를 임시로 1로 설정
    # 1페이지만 접근하여 HTTP 200 확인
    # 성공: {"status": "ok", "source": config.name, "response_time_ms": ...}
    # 실패: {"status": "error", ...}
```

---

## 4. 에러 처리 (4단계)

| 단계 | 발생 시점 | 처리 방식 | 영향 범위 |
|------|----------|-----------|-----------|
| 1. Config 검증 | ScraperConfig 생성 | `ValidationError` (fail-fast) | 수집기 생성 실패 |
| 2. 페이지 요청 | _fetch_page HTTP 에러 | `logger.warning` + break | 해당 페이지 이후 중단 (부분 수집) |
| 3. 행 파싱 | _parse_rows 개별 행 | `logger.debug` + continue | 해당 행만 건너뜀 |
| 4. 필드 파싱 | 날짜/링크 등 개별 필드 | 기본값 (date=None, link="") | 필드만 빈 값 |

이 4단계 구조로 하나의 행이나 필드가 실패해도 나머지 수집은 계속 진행된다.

---

## 5. 주요 설계 결정

### 5-1. 단일 파일 구조

ScraperConfig와 GenericScraper를 `generic_scraper.py` 하나에 배치. config 모델과 엔진 클래스가 밀접하게 결합되어 있어 분리할 이유가 없음.

### 5-2. BaseCollector 상속 + __init__ 오버라이드

GenericScraper는 API 키가 필요 없지만, BaseCollector의 `collect()` → `_fetch()` 흐름과 `health_check()` 인터페이스를 재활용하기 위해 상속. `__init__`에서 `api_key=None`을 전달하여 키 검증 우회.

### 5-3. 0.5초 기본 딜레이

페이지 요청 간 `asyncio.sleep(0.5)` 적용. 과도한 요청으로 인한 IP 차단 방지.

### 5-4. verify_ssl=True 기본값

lets_portal 원본은 `verify=False`를 사용했으나, 보안을 위해 기본값을 `True`로 설정. HTTP 사이트(gntp, cba 등)는 config에서 `verify_ssl=False`로 개별 설정.

### 5-5. httpx.AsyncClient 쿠키 지속

`session_init_url`이 설정되면 실제 수집 전에 해당 URL에 GET 요청을 보내 쿠키를 획득. 동일 클라이언트로 후속 요청을 보내므로 쿠키가 자동 유지됨.

---

## 6. 테스트 요약

총 Phase 2 신규: 63개, Phase 1 포함 전체: 단위 테스트 300개 전체 통과.

### 단위 테스트 상세

| 카테고리 | 테스트 수 | 대상 |
|----------|-----------|------|
| ScraperConfig 검증 | 18 | 필수 필드, source_key 패턴, validator 3종, 선택 필드 기본값 |
| GenericScraper 초기화 | 5 | config dict/모델 변환, BaseCollector 상속 확인 |
| _extract_link | 8 | 일반 href, JS 정규식 단일/복수 캡처, link_base 상대→절대, 링크 없음 |
| _parse_rows | 11 | 정상 파싱, cutoff 필터링, 날짜 없는 행, skip_no_date, grid_selector, 에러 격리 |
| _build_page_url | 5 | {page} 치환, {offset} 치환, 페이지네이션 없음, POST 방식 |
| _make_bid_no | 3 | 해시 결정론성, source_key 포함, 다른 입력→다른 해시 |
| respx 통합 (_fetch) | 7 | 단일/다중 페이지, POST 방식, 세션 초기화, HTTP 에러 graceful, cutoff 조기 종료 |
| health_check | 3 | 정상/HTTP 에러/네트워크 에러 |
| collect() 통합 | 2 | BaseCollector.collect() → _fetch() 흐름, pages_processed 반환 |

---

## 7. 주요 실수 및 교훈

### 실수

1. **respx 모의 테스트에서 무한 페이지네이션**: 초기 테스트에서 respx mock이 모든 페이지에 동일한 HTML을 반환하도록 설정. scraper가 cutoff 이전 데이터를 발견하지 못해 `max_pages`까지 계속 요청. 단일 페이지 테스트에서는 `max_pages=1`로 설정하여 해결. 다중 페이지 테스트에서는 페이지별 다른 HTML(두 번째 페이지에 cutoff 이전 날짜 포함)을 반환하도록 수정.

---

## 8. 향후 주의점

1. **Phase 3 JSON API 모드 추가 예정**: CCEI, 부산, KSD 등 JSON API를 사용하는 사이트를 위해 `api_mode="json"` 옵션 추가 예정. 현재 GenericScraper는 HTML 전용.

2. **bid_no_regex 추가 예정**: 현재 bid_no는 title+link의 MD5 해시로 생성. URL에 안정적인 ID가 포함된 사이트는 `bid_no_regex`로 직접 추출하면 더 안정적인 ID 생성 가능.

3. **lets_portal 39개 config 호환**: lets_portal에 있는 39개 사이트 설정을 ScraperConfig 형식으로 변환하면 그대로 사용 가능. Phase 3에서 이 작업 수행 예정.

4. **HTTP 사이트 SSL 설정**: gntp, cba 등 HTTP 사이트는 config에 `verify_ssl=false` 설정 필요. 기본값이 `True`이므로 누락하면 SSL 에러 발생.

5. **POST 방식 수집기**: itp, gntp 2개 사이트가 POST 방식 사용. `post_data` + `page_param_key` 조합으로 처리. `post_json`은 아직 실제 사용 사이트 없으나 확장성을 위해 구현.

6. **쿠키 기반 사이트**: `session_init_url`로 사전 요청을 보내 쿠키를 획득하는 방식. httpx.AsyncClient가 쿠키를 자동 유지하므로 별도 처리 불필요.

7. **source_key 명명 규칙**: ASCII 소문자+숫자+언더스코어만 허용. bid_no의 일부로 사용되므로 한글이나 대문자 불가. lets_portal config 변환 시 source_key 명명에 주의.

8. **delay 고정값**: 현재 페이지 간 딜레이가 0.5초로 하드코딩. 필요하면 config에 `delay` 필드 추가 고려.

---

## 9. Phase 2 완료 기준 체크리스트

- [x] ScraperConfig Pydantic 모델로 설정 검증
- [x] GenericScraper가 BaseCollector 상속하여 collect()/health_check() 인터페이스 유지
- [x] 4가지 페이지네이션 패턴 지원 (GET page, GET offset, POST, 없음)
- [x] JS 링크 정규식 추출 지원 (단일/복수 캡처)
- [x] 4단계 에러 처리 (config → page → row → field)
- [x] 단위 테스트 63개 전체 통과
- [x] 기존 237개 테스트 영향 없음 (총 300개 통과)

---

## 10. 중소벤처24 (smes24.py) — 스킵

API 15113191은 **LINK 타입 API**로 표준 REST API가 아님. `smes.go.kr` 자체 API를 사용하며 별도 인증키가 필요 (중소벤처24 운영팀 044-300-0990 신청). 기존 `smes.py` (API 15113297)가 동일 데이터소스를 커버하므로 스킵.

---

## 11. 나라장터 확장 메서드 (nara.py)

### 11-1. API 엔드포인트

| 서비스 | Base URL | Key 파라미터 | 용역 | 물품 | 공사 |
|--------|----------|-------------|------|------|------|
| 낙찰 | `/1230000/as/ScsbidInfoService` | `serviceKey` (소문자) | `getScsbidListSttusServcPPSSrch` | `...ThngPPSSrch` | `...CnstwkPPSSrch` |
| 계약 | `/1230000/ao/CntrctInfoService` | `serviceKey` (소문자) | `getCntrctInfoListServc` | `...Thng` | `...Cnstwk` |
| 사전규격 | `/1230000/ao/HrcspSsstndrdInfoService` | `ServiceKey` (**대문자 S**) | `getPublicPrcureThngInfoServc` | `...Thng` | `...Cnstwk` |

### 11-2. Notice 매핑

- **낙찰**: `bid_no=낙찰-{type}-{bidNtceNo}-{bidNtceOrd}`, extra에 낙찰자 정보 (bidwinnrNm, sucsfbidAmt, sucsfbidRate, prtcptCnum)
- **계약**: `bid_no=계약-{type}-{dcsnCntrctNo}`, 계약금액(thtmCntrctAmt), 계약기간(cntrctPrd), 계약방식(cntrctCnclsMthdNm)
- **사전규격**: `bid_no=사전규격-{type}-{bfSpecRgstNo}`, 규격서 첨부파일(specDocFileUrl1~5), 의견마감일(opninRgstClseDt)

### 11-3. 실제 API 검증

- 낙찰 (용역 1일분): 15건
- 계약 (용역 1일분): 6,718건
- 사전규격 (물품 1일분): 322건

### 11-4. 주요 실수 및 교훈

1. **API 경로 prefix 불일치**: 입찰공고는 `/ad/`, 낙찰은 `/as/`, 계약/사전규격은 `/ao/`. 서비스별로 다른 prefix 사용.
2. **사전규격 ServiceKey 대문자**: 다른 모든 서비스는 `serviceKey`(소문자 s), 사전규격만 `ServiceKey`(대문자 S). API 참고자료 docx에서 발견.
3. **활용 신청 필요**: 낙찰(15129397)/계약(15129427)/사전규격(15129437)은 입찰공고와 별도로 data.go.kr에서 활용 신청 필요. 미신청 시 500 반환.
4. **계약 operation명**: PPSSrch 접미사 있는 버전은 추가 필수 파라미터 필요 → 기본 operation 사용.

### 11-5. 향후 주의점

1. 사전규격은 `ServiceKey` (대문자 S) 필수
2. 계약정보는 1일분만으로도 ~6700건 → `max_pages` 조절 필요
3. `_fetch_extended()`가 공통 수집 루프 — 기존 `_fetch()`와 분리하여 입찰공고에 영향 없음

---

## 12. fetch_detail() 상세 조회 메서드

### 12-1. 왜 필요한가

BidWatch 모달 팝업에서 공고 클릭 시 사업개요(content)를 보여줘야 하는데, 목록 API에는 content가 없거나 잘려있다.
수집 시점에 전체 content를 가져오면 트래픽/저장 비용이 과다하므로, **사용자가 클릭할 때만 실시간 조회 → DB 캐시** 방식.

### 12-2. 구현 방식 결정

| 수집기 | 선택지 | 결정 | 이유 |
|--------|--------|------|------|
| KstartupCollector | API 단건 필터 | `cond[pbanc_sn::EQ]` 파라미터 | 기존 목록 API와 동일 엔드포인트, content 전문 반환 |
| NaraCollector | (A) 상세 조회 API vs (B) g2b.go.kr 스크래핑 | B | 상세 조회 전용 API가 존재하지 않음. 목록 API에도 content 필드 없음 |

### 12-3. 외부 제약

- **나라장터 목록 API에 content(사업개요) 필드가 없다** — 입찰공고정보서비스 docx 참고자료에서 전체 operation 목록 확인, 상세 조회 전용 operation 없음
- **data.go.kr API 서버 장애** — 구현 당시 502 반환으로 실제 API 테스트 불가. 서버 복구 후 NaraCollector.fetch_detail 통합 테스트 필요
- **BaseCollector.fetch_detail 기본값 None** — 지원하지 않는 수집기(기업마당, 보조금24 등)는 오버라이드 불필요

### 12-4. 향후 주의점

1. NaraCollector.fetch_detail은 g2b.go.kr 스크래핑 — 사이트 구조 변경 시 셀렉터 수정 필요
2. data.go.kr 서버 복구 후 NaraCollector 통합 테스트 실행 필요
3. BidWatch에서 fetch_detail 결과는 DB에 캐시하여 반복 호출 방지

---

## 13. Phase 3 작업 예고

Phase 3에서는 GenericScraper 엔진을 활용하여 lets_portal 39개 사이트의 config를 작성하고, JSON API 모드를 추가한다:

1. **lets_portal 39개 사이트 config 작성** — ScraperConfig 형식으로 변환
2. **JSON API 모드** (`api_mode="json"`) — CCEI, 부산, KSD 등
3. **bid_no_regex** — URL 기반 안정적 ID 추출
