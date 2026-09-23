# Phase 2: GenericScraper + 나라장터 확장 - 작업 로그

> 작성일: 2026-04-11
> 상태: 완료
> 테스트: 단위 300개 전체 통과, 실제 API 검증 완료

---

## 1. 이번 Phase에서 한 일

- **GenericScraper 엔진**: config JSON만으로 임의 HTML 게시판을 수집하는 범용 엔진
- **나라장터 확장**: 기존 입찰공고에 낙찰/계약/사전규격 수집 메서드 추가
- **중소벤처24**: 스킵 (이유는 아래)

---

## 2. 왜 이렇게 결정했는가

### 2-1. GenericScraper를 별도 수집기가 아닌 "엔진"으로 만든 이유

선택지: (A) 사이트마다 수집기 클래스를 만든다 vs (B) config 하나로 범용 엔진을 만든다

B를 선택한 이유:
- BidWatch의 핵심 차별점이 "AI가 config를 자동 생성 → 개발 없이 새 사이트 추가"
- lets_portal에 이미 39개 사이트 config가 검증되어 있어 그대로 재활용 가능
- 사이트마다 클래스를 만들면 100개 사이트 = 100개 파일 → 유지보수 불가

### 2-2. BaseCollector를 상속하되 __init__을 오버라이드한 이유

BaseCollector는 api_key를 필수로 요구하지만 GenericScraper는 API 키가 없다. 선택지:
- (A) BaseCollector에 api_key optional 로직 추가
- (B) GenericScraper에서 __init__ 오버라이드

B를 선택. 이유: 기존 5개 수집기는 api_key 필수가 맞으므로 BaseCollector를 건드리면 안 된다.

### 2-3. 중소벤처24 (smes24.py) 스킵 이유

API 15113191은 **LINK 타입**으로 표준 REST API가 아님:
- `smes.go.kr` 자체 API 사용, 별도 인증키를 운영팀(044-300-0990)에 전화 신청해야 함
- 기존 `smes.py`(API 15113297)가 사실상 같은 데이터(중소벤처기업부 사업공고)를 커버
- 투입 대비 추가 가치가 없어 스킵

### 2-4. 나라장터 확장을 별도 클래스가 아닌 메서드로 추가한 이유

낙찰/계약/사전규격은 같은 `DATA_GO_KR_KEY`를 사용하고, 같은 XML 파싱 로직을 공유한다.
별도 클래스로 만들면 NaraCollector와 코드 중복이 심해지므로, 기존 클래스에 `collect_awards()`, `collect_contracts()`, `collect_pre_specs()` 메서드를 추가하고 공통 루프를 `_fetch_extended()`로 뽑았다.

---

## 3. 외부 제약 조건 (코드에서 알 수 없는 것)

### 3-1. 나라장터 API 경로 규칙

**같은 조달청 API인데 서비스마다 경로 prefix가 다르다:**

| 서비스 | prefix | 예시 |
|--------|--------|------|
| 입찰공고 | `/ad/` | `/1230000/ad/BidPublicInfoService` |
| 낙찰정보 | `/as/` | `/1230000/as/ScsbidInfoService` |
| 계약정보 | `/ao/` | `/1230000/ao/CntrctInfoService` |
| 사전규격 | `/ao/` | `/1230000/ao/HrcspSsstndrdInfoService` |

이 규칙은 어디에도 문서화되어 있지 않고, data.go.kr 상세 페이지에서만 확인 가능.

### 3-2. 사전규격 ServiceKey 대문자 S

**모든 조달청 API가 `serviceKey`(소문자 s)를 쓰는데, 사전규격만 `ServiceKey`(대문자 S).**
소문자로 보내면 500 "Unexpected errors" 반환 (에러 메시지도 없음).
조달청 OpenAPI 참고자료 docx 파일의 예시 URI에서 발견.

### 3-3. 별도 활용 신청 필요

입찰공고 서비스를 신청했어도 낙찰/계약/사전규격은 **각각 별도로 data.go.kr에서 활용 신청해야 함:**
- 15129397 — 낙찰정보서비스
- 15129427 — 계약정보서비스
- 15129437 — 사전규격정보서비스

미신청 시 500 반환. 403이 아니라 500이라 원인 파악이 어렵다.

### 3-4. 계약정보 operation명 주의

계약 서비스에는 두 종류 operation이 있다:
- `getCntrctInfoListServc` — 정상 동작 (inqryDiv=1 필수)
- `getCntrctInfoListServcPPSSrch` — resultCode=08 "필수값 입력 누락" 반환

PPSSrch 버전은 추가 필수 파라미터가 더 있다. 기본 operation을 사용해야 한다.

### 3-5. 계약정보 데이터량

계약정보는 용역 1일분만으로도 **~6,700건**. 전체(용역+물품+공사) 수집 시 대량 데이터 처리 고려 필요.

---

## 4. 실패한 접근과 원인

### 4-1. 사전규격 API 경로 탐색 삽질 (약 40분)

**시도한 것:**
1. 기존 입찰공고 패턴(`/ad/BfSpecRgstInfoService`) → 404
2. `/as/`, `/ao/` prefix 조합 → 404
3. `a*`~`c*` prefix 전수 스캔 → 전부 404/500
4. 서비스명 변형 (`BfSpecInfoService`, `PrespGnrlzInfoService`) → 404

**원인:** 서비스명이 `BfSpecRgstInfoService`가 아니라 `HrcspSsstndrdInfoService`였음. 추측으로는 절대 맞출 수 없는 이름.

**해결:** 사용자에게 data.go.kr 상세 페이지의 Base URL 확인 요청 → 즉시 해결.

**교훈:** API 경로를 추측으로 찾으려 하지 말고, 3회 실패 시 바로 사용자에게 문서 요청.

### 4-2. ServiceKey 대소문자 문제 (약 20분)

올바른 경로를 찾은 후에도 계속 500 반환. `serviceKey`(소문자)로 보내고 있었기 때문.
사용자가 제공한 조달청 docx 참고자료의 예시 URI에서 `ServiceKey`(대문자)를 발견하여 해결.

### 4-3. respx mock 테스트 무한 페이지네이션

초기 테스트에서 5개 실패. mock이 모든 URL에 동일 HTML을 반환하여 scraper가 `max_pages=3`까지 순회.
단일 페이지 테스트에 `max_pages=1` 명시하여 해결.

---

## 5. 향후 주의점

1. **사전규격은 `ServiceKey` (대문자 S) 필수** — 다른 모든 조달청 서비스와 다름
2. **새 조달청 서비스 추가 시** data.go.kr 상세 페이지에서 Base URL 확인 필수 (추측 불가)
3. **data.go.kr 활용 신청** — 서비스별 개별 신청, 미신청 시 500 (403이 아님)
4. **GenericScraper의 HTTP 사이트** — gntp(`http://account.more.co.kr`), cba 등은 config에 `verify_ssl: false` 필요
5. **lets_portal 39개 config** — ScraperConfig 형식과 호환되나 Phase 3에서 실제 변환/테스트 필요
