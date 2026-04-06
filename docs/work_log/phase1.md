# Phase 1: MVP 수집기 3종 구현 - 작업 로그

> 작성일: 2026-04-06
> 상태: 완료
> 테스트: 단위 174개 전체 통과 + 통합(실제 API) 11개 전체 통과

---

## 1. 구현 범위

Phase 0에서 만든 BaseCollector를 상속하여 MVP 수집기 3개를 구현했다:

| 수집기 | 파일 | 출처 | API 형식 | 인증 키 |
|--------|------|------|----------|---------|
| 나라장터 | `nara.py` | 공공데이터포털 | XML | `DATA_GO_KR_KEY` |
| 기업마당 | `bizinfo.py` | bizinfo.go.kr | JSON | `BIZINFO_API_KEY` (별도) |
| 보조금24 | `subsidy24.py` | 공공데이터포털 (odcloud) | JSON | `DATA_GO_KR_KEY` (별도 활용 신청 필요) |

### 1-1. 디렉토리 변경사항

```
bid_collectors/
├── __init__.py          # NaraCollector, BizinfoCollector, Subsidy24Collector 추가 export
├── nara.py              # 나라장터 수집기 (신규)
├── bizinfo.py           # 기업마당 수집기 (신규)
└── subsidy24.py         # 보조금24 수집기 (신규)

tests/
├── test_nara.py         # 31개 테스트 (신규)
├── test_bizinfo.py      # 36개 테스트 (신규)
└── test_subsidy24.py    # 41개 테스트 (신규)
```

### 1-2. 패키지 공개 API 변경 (`__init__.py`)

```python
__all__ = [
    "Notice", "CollectResult", "BaseCollector",
    "NaraCollector", "BizinfoCollector", "Subsidy24Collector",
]
```

---

## 2. 나라장터 수집기 (`bid_collectors/nara.py`)

### 2-1. API 정보

- **엔드포인트**: `https://apis.data.go.kr/1230000/ad/BidPublicInfoService04`
- **3개 서비스 (BID_SERVICES)**:
  - 용역: `getBidPblancListInfoServcPPSSrch`
  - 물품: `getBidPblancListInfoThngPPSSrch`
  - 공사: `getBidPblancListInfoCnstwkPPSSrch`
- **응답 형식**: XML
- **인증**: `serviceKey` 파라미터 (DATA_GO_KR_KEY, 소문자 s 필수)

### 2-2. 날짜 분할 (`_split_date_range`)

API의 한 번 조회 범위 제한 때문에 날짜를 7일 단위로 분할한다.

- 입력: `days` (수집 기간)
- 출력: `list[tuple[str, str]]` — (시작, 종료) yyyyMMddHHmm 형식
- 시작 시각: `0000`, 종료 시각: `2359`
- 예: days=15 → 3개 chunk (7+7+1)

```python
# 핵심 로직
cur = start
while cur < end:
    chunk_end = min(cur + timedelta(days=7), end)
    ranges.append((cur.strftime("%Y%m%d") + "0000", chunk_end.strftime("%Y%m%d") + "2359"))
    cur = chunk_end
```

### 2-3. XML 파싱 (`_parse_xml_items`)

- `lxml.etree.fromstring(xml_bytes)` 사용
- `resultCode != "00"` 이면 `ValueError` 발생
- `totalCount`와 `item` 목록 반환

### 2-4. Notice 변환 (`_item_to_notice`)

**bid_no 형식**: `{bid_type}-{bidNtceNo}-{bidNtceOrd}`
- bidNtceOrd가 없으면 `{bid_type}-{bidNtceNo}`

**URL 구성**: `https://www.g2b.go.kr:8081/ep/invitation/publish/bidInfoDtl.do?bidno={bidNtceNo}&bidseq={bidNtceOrd}`

**예산 처리**:
- `asignBdgtAmt` → budget
- `presmptPrce` → est_price
- `notice.budget = budget or est_price` (예산이 우선, 없으면 추정가격)

**카테고리 조합**: `prdctClsfcNoNm > (mtrlClsfcNoNm or dtlPrdctClsfcNoNm)`

**첨부파일**: `bidNtceFlNm1~10`, `bidNtceFlUrl1~10` — 이름과 URL이 모두 있는 것만 수집 (최대 10개)

**extra 필드**:
| 키 | 원본 태그 | 설명 |
|---|---|---|
| `bid_type` | (인자) | 용역/물품/공사 |
| `est_price` | `presmptPrce` | 추정가격 |
| `budget` | `asignBdgtAmt` | 배정예산 |
| `bid_method` | `bidMethdNm` | 입찰방식 |
| `contract_method` | `cntrctMthdNm` | 계약방식 |
| `contact` | `ntceInsttOfclNm` + `ntceInsttOfclTelNo` | 담당자 연락처 |
| `bid_qual` | `bidQlftcRgstDt` | 입찰자격등록일 |
| `open_date` | `opengDt` | 개찰일시 |

빈 값은 extra에서 제외 (dict comprehension `if v` 필터).

### 2-5. _fetch 흐름

```
for bid_type in bid_types:               # 기본: ["용역", "물품", "공사"]
    for (start_dt, end_dt) in date_ranges:  # 7일 단위 분할
        page = 1
        while True:
            params = {serviceKey, inqryDiv=1, inqryBgnDt, inqryEndDt, numOfRows=100, pageNo, type=xml}
            resp = _request_with_retry(client, operation, params)
            if resp is None: break
            items, total = _parse_xml_items(resp.content)
            for item in items: notice = _item_to_notice(item, bid_type)
            if page * 100 >= total: break
            page += 1
```

### 2-6. 429 재시도 (`_request_with_retry`)

- 429 응답 → `asyncio.sleep(30)` 후 재시도
- 최대 3회 시도 (MAX_RETRIES)
- 다른 에러 → 5초 대기 후 재시도
- 모든 시도 실패 → `None` 반환 (해당 chunk 건너뜀)

### 2-7. health_check

- 용역 서비스에 1건 조회 요청
- 성공: `{"status": "ok", "source": "나라장터", "response_time_ms": ...}`
- 실패: `{"status": "error", "source": "나라장터", "message": ..., "response_time_ms": ...}`

---

## 3. 기업마당 수집기 (`bid_collectors/bizinfo.py`)

### 3-1. API 정보

- **엔드포인트**: `https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do`
- **응답 형식**: JSON (`jsonArray` 필드)
- **인증**: `crtfcKey` 파라미터 (`BIZINFO_API_KEY` — DATA_GO_KR_KEY와 별도)
- **_env_key()** 오버라이드: `"BIZINFO_API_KEY"` 반환

### 3-2. 날짜 필터링 (cutoff 기반)

나라장터처럼 API 파라미터로 날짜를 보내는 것이 아니라, 전체 데이터를 페이지별로 받으면서 `creatPnttm` 필드를 확인하여 cutoff 이전 데이터를 제외한다.

```python
cutoff = datetime.now() - timedelta(days=days)
# 각 item에 대해:
creat_dt = datetime.strptime(creatPnttm[:10], "%Y-%m-%d")
if creat_dt < cutoff: return None  # 건너뜀
```

**조기 종료 조건**: 한 페이지에서 cutoff 이전 항목이 발견되고, 마지막 3건도 모두 cutoff 이전이면 다음 페이지 요청 중단.

### 3-3. Notice 변환 (`_item_to_notice`)

**bid_no 형식**: `BIZINFO-{pblancId}`

**신청기간 파싱**: `reqstBeginEndDe` 필드 (예: `"2024-03-01 ~ 2024-04-05"`)
- parse_date()로 시작일 추출 (기간 형식에서 시작일 반환)
- `~` 기준으로 split하여 종료일을 별도 추출

**content**: `bsnsSumryCn` 필드를 `clean_html_to_text()`로 HTML 제거

**첨부파일** (`_parse_attachments`):
- `printFileNm` / `printFlpthNm` → 첫 번째 첨부 (공고문 등)
- `fileNm` / `flpthNm` → 두 번째 첨부 (포스터 등)
- 최대 2개, 이름과 URL 모두 있어야 포함

**extra 필드**:
| 키 | 원본 필드 | 설명 |
|---|---|---|
| `sub_category` | `pldirSportRealmMlsfcCodeNm` | 소분류 |
| `target` | `trgetNm` | 지원대상 |
| `hashtags` | `hashtags` | 해시태그 |
| `reference` | `refrncNm` | 참고사항 |
| `req_method` | `reqstMthPapersCn` | 신청방법 |
| `view_count` | `inqireCo` | 조회수 |

### 3-4. _fetch 흐름

```
page = 1
while page <= max_pages (기본 50):
    params = {crtfcKey, dataType=json, pageUnit=100, pageIndex}
    resp = client.get(API_URL, params=params)
    items = data["jsonArray"]
    if not items: break
    for item in items:
        notice = _item_to_notice(item, cutoff)  # cutoff 이전이면 None
    # 종료 조건: page * 100 >= totCnt 또는 조기 종료
    page += 1
```

### 3-5. health_check

- 1건 조회 요청, `jsonArray`가 비어있으면 에러 처리

---

## 4. 보조금24 수집기 (`bid_collectors/subsidy24.py`)

### 4-1. API 정보

- **엔드포인트**: `https://api.odcloud.kr/api/gov24/v3/serviceList`
- **Swagger 문서**: `https://infuser.odcloud.kr/api/stages/44436/api-docs`
- **응답 형식**: JSON (`data` 필드)
- **인증**: `serviceKey` 파라미터 (DATA_GO_KR_KEY)
- **주의**: data.go.kr에서 서비스 15113968 별도 활용 신청 필요

### 4-2. 날짜 필터링 (API 파라미터)

API 자체적으로 날짜 조건 필터 지원:

```python
params = {
    "serviceKey": self.api_key,
    "page": str(page),
    "perPage": str(100),
    "cond[수정일시::GTE]": cutoff_str,  # "2026-04-05 10:00:00" 형식
}
```

### 4-3. 한국어 필드명

API 응답의 필드명이 모두 한국어:
- `서비스ID`, `서비스명`, `소관기관명`, `서비스목적요약`, `지원내용`, `신청기한`, `상세조회URL` 등

### 4-4. Notice 변환 (`_item_to_notice`)

**bid_no 형식**: `GOV24-{서비스ID}`

**필수 필드 검증**: `서비스ID`와 `서비스명`이 모두 없으면 `None` 반환

**URL 구성**:
- `상세조회URL`이 있으면 사용
- 없으면 `https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{서비스ID}` 생성

**content 합성**: `서비스목적요약` + `지원내용` (HTML 제거) 줄바꿈으로 결합

**start_date**: 항상 None (보조금24 API에 시작일 필드 없음)

**extra 필드**:
| 키 | 원본 필드 | 설명 |
|---|---|---|
| `support_type` | `지원유형` | 현금/현물 등 |
| `target` | `지원대상` | 대상자 |
| `selection_criteria` | `선정기준` | 선정 기준 |
| `apply_method` | `신청방법` | 신청 방법 |
| `deadline_raw` | `신청기한` | 원본 마감일 문자열 |
| `department` | `부서명` | 소관 부서 |
| `agency_type` | `소관기관유형` | 기관 유형 |
| `user_type` | `사용자구분` | 시민/기업 등 |
| `reception_agency` | `접수기관` | 접수기관명 |
| `phone` | `전화문의` | 문의 전화번호 |
| `view_count` | `조회수` | 조회수 |

### 4-5. 기업 대상 필터링 (`_is_business_target`)

`only_business=True` 옵션으로 기업/사업자 관련 서비스만 필터링 가능.

**검사 필드**: 서비스명, 지원대상, 사용자구분, 서비스분야

**매칭 키워드 (BUSINESS_KEYWORDS)**:
```python
["기업", "사업자", "소상공인", "창업", "중소", "벤처",
 "스타트업", "법인", "자영업", "중견", "수출"]
```

하나라도 매칭되면 기업 대상으로 판정.

### 4-6. _fetch 흐름

```
page = 1
while page <= max_pages (기본 50):
    params = {serviceKey, page, perPage=100, cond[수정일시::GTE]=cutoff_str}
    resp = client.get(API_URL, params=params)
    if data["code"] < 0: break  # API 에러
    items = data["data"]
    if not items: break
    for item in items:
        notice = _item_to_notice(item)
        if only_business and not _is_business_target(item): continue
    if page * 100 >= matchCount: break
    page += 1
```

### 4-7. health_check

- 1건 조회 요청, `code < 0`이면 에러 처리

---

## 5. 공통 패턴

### 5-1. 모든 수집기의 _fetch 공통 구조

1. `create_client(timeout=30.0)` 으로 httpx 클라이언트 생성
2. 페이지 순회하며 API 호출
3. `pages_processed` 카운트
4. `tuple[list[Notice], int]` 반환 — 두 번째 요소가 pages_processed (kwargs mutate 방식에서 변경됨)
5. 에러 발생 시 로깅 후 break (부분 수집 허용)

### 5-2. extra 딕셔너리 패턴

모든 수집기에서 동일한 패턴으로 빈 값 제외:
```python
extra = {
    k: v for k, v in {
        "field1": value1,
        "field2": value2,
    }.items() if v
} or None
```

### 5-3. health_check 공통 구조

```python
async def health_check(self) -> dict:
    start = time.time()
    try:
        # 1건 조회 요청
        ms = int((time.time() - start) * 1000)
        return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}
```

---

## 6. 테스트 요약

총 Phase 1 신규: 108개, Phase 0 포함 전체: 단위 테스트 174개 전체 통과.

### 통합 테스트 (실제 API): 11개 전체 통과

| 수집기 | 테스트 수 | 비고 |
|--------|-----------|------|
| 나라장터 | 3 | 용역 1일분 383건, 4페이지 처리 |
| 기업마당 | 3 | |
| 보조금24 | 3 | |
| 크로스체크 | 2 | |

### 단위 테스트 상세

| 파일 | 테스트 수 | 대상 |
|------|-----------|------|
| `test_nara.py` | 31 | _split_date_range 6개 + _parse_xml_items 4개 + _item_to_notice 10개 + 초기화 3개 + _fetch 5개 + health_check 3개 |
| `test_bizinfo.py` | 36 | _is_within_cutoff 5개 + _parse_attachments 5개 + _item_to_notice 11개 + 초기화 5개 + _fetch 6개 + health_check 4개 |
| `test_subsidy24.py` | 41 | _item_to_notice 17개 + _is_business_target 7개 + 초기화 4개 + _fetch 9개 + health_check 4개 |

**테스트 주요 검증 사항:**

### 나라장터 (`test_nara.py`)
- **_split_date_range**: 1일/10일/14일/15일에 대한 chunk 수, yyyyMMddHHmm 형식, 범위 연속성
- **_parse_xml_items**: 정상 응답, 다중 item, 에러 응답(resultCode=99) ValueError, 빈 응답
- **_item_to_notice**: 전체 필드 매핑, bid_no 형식(`bid_type-bidNtceNo-bidNtceOrd`), URL 생성, budget/est_price 우선순위, 첨부파일 파싱, 카테고리 조합, extra 필드, 최소 필드 처리, 날짜 파싱
- **NaraCollector**: API 키 필수 확인, source_name 확인
- **_fetch (respx mock)**: 단일 페이지, 다중 페이지 페이지네이션(totalCount=150), 빈 응답, 429 재시도 성공, 429 재시도 소진
- **health_check**: 정상/HTTP에러/API에러 응답

### 기업마당 (`test_bizinfo.py`)
- **_is_within_cutoff**: 최근/오래된/빈/잘못된/정확한 cutoff 날짜
- **_parse_attachments**: 양쪽 파일, printFile만, fileNm만, 파일 없음, 이름만 있고 URL 없음
- **_item_to_notice**: 전체 매핑, bid_no(`BIZINFO-{pblancId}`), URL, HTML content 정리, 기간 파싱(시작~종료), 첨부파일, extra, old item None 반환, 빈 creatPnttm, 최소 항목
- **BizinfoCollector**: API 키 필수, source_name, env_key("BIZINFO_API_KEY"), 생성자/환경변수 키
- **_fetch**: 단일/다중 페이지, 빈 jsonArray, jsonArray 키 없음, HTTP/네트워크 에러 graceful
- **health_check**: 정상/HTTP에러/빈응답 에러/네트워크 에러

### 보조금24 (`test_subsidy24.py`)
- **_item_to_notice**: 전체 매핑, bid_no(`GOV24-{서비스ID}`), 상세조회URL 있음/없음/키 없음, content 합성(요약+지원내용/요약만/지원내용만), end_date 파싱/빈 값, start_date 항상 None, extra 11개 필드, extra None(모두 빈 값), 서비스ID/서비스명 없으면 None
- **_is_business_target**: 기업/창업/소상공인/벤처 키워드 매칭, 비매칭, 빈 항목, SAMPLE_ITEM 매칭
- **Subsidy24Collector**: API 키 필수, source_name, 생성자/환경변수 키
- **_fetch**: 단일/다중 페이지, API 에러(code<0), only_business True/False, 빈 data, HTTP/네트워크 에러 graceful, 무효 항목 건너뜀
- **health_check**: 정상/API에러/HTTP에러/네트워크 에러

---

## 7. 주요 실수 및 교훈

### 실수

1. **보조금24 API 엔드포인트 탐색 어려움**: 공공데이터포털(data.go.kr) 페이지에서 직접 API URL을 찾기 어려웠음. Swagger 문서가 `https://infuser.odcloud.kr/api/stages/44436/api-docs`에 별도로 존재했으며, 이를 통해 올바른 엔드포인트와 파라미터를 확인할 수 있었음.

2. **보조금24 API 키 별도 활용 신청 필요**: DATA_GO_KR_KEY를 가지고 있어도 서비스 15113968에 대해 별도 활용 신청을 해야 함. 신청하지 않으면 "등록되지 않은 인증키" 에러 발생. → **해결 완료**: 활용 신청 후 실제 API 호출 테스트 성공 (100건 수집, pages_processed 정상 확인).

3. **기업마당은 별도 API 키 사용**: 다른 수집기들이 DATA_GO_KR_KEY를 공유하는 것과 달리, 기업마당은 `BIZINFO_API_KEY`라는 별도 키 사용. `_env_key()` 오버라이드로 처리.

4. **curl 직접 호출 시 인코딩 문제**: curl로 API를 직접 호출하면 UTF-8 surrogate 이슈로 한국어가 깨졌으나, Python httpx + JSON 파서에서는 정상 처리됨. 디버깅 시 혼란을 줄 수 있으므로 Python 코드로 테스트하는 것이 정확함.

5. **`_fetch` 반환값 kwargs 버그**: Phase 0에서 설계한 `kwargs["_pages_processed"]` 패턴이 원래부터 동작하지 않았음. Python에서 `**kwargs`를 다시 `**kwargs`로 전달하면 새 dict가 생성되므로, `_fetch()` 내부에서 kwargs를 mutate해도 `collect()`의 원본 kwargs에 반영되지 않음. 보조금24 실제 API 테스트에서 `pages_processed: 0`으로 나와 발견.

6. **`_fetch` 반환값 수정**: `_fetch()` 반환값을 `list[Notice]` → `tuple[list[Notice], int]`로 변경. 두 번째 요소가 pages_processed. base.py, nara.py, bizinfo.py, subsidy24.py + 테스트 4개 파일 수정.

7. **나라장터 파라미터명 오류**: `ServiceKey` (대문자 S)를 사용하여 404 에러 발생. lets_portal 원본은 `serviceKey` (소문자 s). data.go.kr API는 파라미터명이 대소문자를 구분하며, 소문자 `serviceKey`가 올바른 형식. 추가로 `inqryDiv=1` 파라미터도 누락되어 있었음.

8. **curl vs Python 라이브러리 차이**: curl로 직접 테스트할 때는 404였지만, Python requests/httpx로는 동작하는 경우가 있었음. 파라미터 인코딩 차이 때문. 실제 API 테스트는 항상 Python 코드로 하는 것이 정확함.

---

## 8. 향후 주의점

1. **API 키 관리**: 3가지 키가 필요함
   - `DATA_GO_KR_KEY`: 나라장터, 보조금24 공통
   - `BIZINFO_API_KEY`: 기업마당 전용
   - 보조금24는 DATA_GO_KR_KEY이지만 서비스 15113968 별도 활용 신청 필수

2. **나라장터 429 대응**: 나라장터 API는 요청 속도 제한이 있어 429 에러가 빈번함. 30초 대기 + 3회 재시도로 처리하지만, 대량 수집 시 충분한 시간 확보 필요.

3. **기업마당 날짜 필터링 방식**: API 자체에 날짜 필터 파라미터가 없어 전체를 받으면서 `creatPnttm`으로 클라이언트 측 필터링. 데이터가 시간순 정렬되지 않을 수 있으므로 조기 종료 조건에 마지막 3건 확인 로직 포함.

4. **보조금24 날짜 파라미터 문법**: `cond[수정일시::GTE]` 형식으로 한국어 필드명을 URL 파라미터에 직접 사용. URL 인코딩이 필요하나 httpx가 자동 처리.

5. **extra 필드 None 처리**: 모든 수집기에서 빈 값은 extra에 포함하지 않고, extra 자체가 비어있으면 None으로 설정. BidWatch DB 저장 시 JSONB null 처리 고려.

6. **bid_types 파라미터**: 나라장터는 `bid_types` kwargs로 특정 서비스만 수집 가능 (기본: 용역+물품+공사 전체). 기업마당과 보조금24는 단일 서비스.

7. **only_business 파라미터**: 보조금24의 `only_business=True` 옵션은 키워드 매칭 기반이므로 완벽하지 않음. 필요하면 키워드 목록(BUSINESS_KEYWORDS) 보강 필요.

8. **max_pages 안전장치**: 기업마당, 보조금24 모두 `max_pages=50` 기본값으로 무한 루프 방지. 필요시 kwargs로 조절 가능.

9. **`_fetch` 시그니처**: 모든 수집기의 `_fetch()`는 반드시 `tuple[list[Notice], int]`를 반환해야 함. Phase 2 수집기 구현 시 이 패턴 준수 필요.

10. **data.go.kr API 파라미터명**: 반드시 `serviceKey` (소문자 s) 사용. `ServiceKey`(대문자)는 동작하지 않음. Phase 2 수집기에서도 주의.

11. **`inqryDiv` 파라미터**: 나라장터 API에 필수. 값은 `"1"`.

---

## 9. Phase 1 완료 기준 체크리스트

- [x] 3개 수집기 각각 collect(days=1) 호출 시 Notice 리스트 반환
- [x] health_check()로 API 연결 확인 가능
- [x] 단위 테스트 통과율 100% (174개)
- [x] 통합 테스트 (실제 API) 통과 (11개)
- [x] pip install -e . 로 로컬 설치 후 import 가능
- [x] BidWatch 본체에서 from bid_collectors import NaraCollector 동작 확인

---

## 10. Phase 2 작업 예고

Phase 2에서는 공기업/공공기관 수집기를 추가 구현한다:

1. **LH (한국토지주택공사)** — 입찰공고
2. **한국전력** — 입찰공고
3. **한국도로공사** — 입찰공고
4. **한국수자원공사** — 입찰공고
5. **방위사업청** — 입찰공고

각 수집기는 동일하게 BaseCollector를 상속하여 `_fetch()` 메서드를 구현한다.
