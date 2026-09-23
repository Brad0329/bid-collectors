# Phase 2 보완: 나라장터 상세 조회 API 조사 + _item_to_notice 강화 - 작업 로그

> 작성일: 2026-04-13
> 상태: 완료
> 선행 작업: phase2_new.md (fetch_detail 구현)

---

## 1. 이번 세션에서 한 일

- **data.go.kr BidPublicInfoService 25개 operation 전수 조사** — 사업개요(content)를 반환하는 상세 조회 API 존재 여부 확인
- **비-PPSSrch operation 단건 필터 가능 여부 테스트** — bidNtceNo 파라미터 동작 검증
- **lets_portal 참고 분석** — bid-collectors가 놓치고 있던 필드 식별
- **_item_to_notice() 강화** — 수집 시점에 lets_portal 동일 수준의 상세 필드를 extra에 저장
- **fetch_detail() 변경** — g2b.go.kr 스크래핑 코드 제거, None 반환으로 전환
- **bs4 의존성 제거** — BeautifulSoup import 불필요

---

## 2. 왜 이렇게 결정했는가

### 2-1. fetch_detail()을 None 반환으로 변경한 이유

선택지:
- (A) g2b.go.kr 스크래핑 유지 — ConnectTimeout으로 동작하지 않음
- (B) 비-PPSSrch API로 단건 조회 — 테스트 결과 미지원 확인
- (C) 수집 시점에 모든 가용 필드 저장 + fetch_detail은 None

C를 선택한 이유:
1. g2b.go.kr 스크래핑은 ConnectTimeout으로 실패하며, 조달청이 외부 스크래핑을 차단하고 있을 가능성 높음
2. 비-PPSSrch operation(getBidPblancListInfoServc 등)은 `inqryBgnDt`, `inqryEndDt`, `inqryDiv`가 필수이며, bidNtceNo를 넘겨도 무시하고 전체 결과 반환 (23,034건)
3. 수집 시점에 API가 제공하는 모든 필드를 extra에 저장하면, bidwatch에서 별도 상세 조회 없이 extra 데이터를 모달에 표시 가능

### 2-2. _item_to_notice() 강화의 근거

lets_portal/backend/collectors/nara.py와 프론트엔드 모달(app.js)을 비교하여, bid-collectors가 누락하고 있던 필드를 식별:
- `sucsfbidMthdNm` (낙찰방식) — lets_portal 모달에서 주요 표시 항목
- `techAbltEvlRt` / `bidPrceEvlRt` (기술/가격 평가 비율) — 입찰 전략 수립에 핵심
- `bidNtceDtlUrl` (API 제공 상세 URL) — g2b.go.kr 직접 구성보다 안정적
- `bidNtceFlNm/Url` + `ntceSpecDocUrl` (첨부파일 + 규격서) — 병합하여 완전한 첨부 목록

---

## 3. 외부 제약 조건 (코드에서 알 수 없는 것)

### 3-1. data.go.kr BidPublicInfoService에 상세 조회 API가 없다

25개 operation 전수 조사 결과, 모든 operation이 List 형태이며 단건 조회(get by ID) 전용 operation이 존재하지 않는다. content(사업개요)를 반환하는 필드도 없다.

### 3-2. 비-PPSSrch operation은 bidNtceNo 단건 필터를 지원하지 않는다

`getBidPblancListInfoServc` 등 비-PPSSrch operation의 필수 파라미터:
- `inqryBgnDt` (조회 시작일) — 필수
- `inqryEndDt` (조회 종료일) — 필수
- `inqryDiv` (조회 구분: 1=등록일시, 2=개찰일시) — 필수

bidNtceNo 파라미터를 함께 전달해도 필터링되지 않고 전체 결과가 반환된다. 에러코드 08("필수값 입력 오류")이 반환되거나, 필수값을 넣으면 전체 23,034건이 반환된다.

### 3-3. PPSSrch vs 비-PPSSrch 필드 차이

| 필드 | PPSSrch | 비-PPSSrch |
|------|---------|-----------|
| `sucsfbidMthdNm` (낙찰방식) | O | O |
| `ntceInsttOfclEmailAdrs` (담당자 이메일) | X (미제공) | O |
| `techAbltEvlRt` (기술평가비율) | O (40.3% 커버리지) | 미확인 |
| `bidNtceDtlUrl` (상세URL) | O (100%) | O |

PPSSrch API를 사용하는 현 구조에서 담당자 이메일은 가져올 수 없다.

### 3-4. bidNtceDtlUrl 필드의 안정성

PPSSrch API 응답 1,709건 중 1,709건(100%)이 `bidNtceDtlUrl` 값을 포함. g2b.go.kr URL을 직접 구성하는 것보다 API 제공 URL을 사용하는 것이 안정적이다.

---

## 4. 구체적 변경 내역

### 4-1. _item_to_notice() 신규 필드

| extra 키 | API 필드 | 커버리지 | 설명 |
|----------|---------|---------|------|
| `award_method` | `sucsfbidMthdNm` | 100% | 낙찰방식 (최저가, 협상에 의한 계약 등) |
| `contact_email` | `ntceInsttOfclEmailAdrs` | 0% (PPSSrch 미제공) | 담당자 이메일 — 코드만 존재 |
| `tech_eval_ratio` | `techAbltEvlRt` | 40.3% | 기술능력평가 비율 |
| `price_eval_ratio` | `bidPrceEvlRt` | 40.3% | 입찰가격 평가 비율 |

### 4-2. URL 개선

- 기존: g2b.go.kr URL 직접 구성 (`f"https://www.g2b.go.kr:8081/ep/..."`)
- 변경: API 제공 `bidNtceDtlUrl` 우선 사용, 없으면 기존 방식 fallback

### 4-3. 첨부파일 병합

- 기존: `bidNtceFlNm` + `bidNtceFlUrl` 쌍만 수집
- 변경: `ntceSpecDocUrl` (규격서 URL)도 병합하여 첨부 목록에 추가
- 효과: 규격서 14건 추가 확보

### 4-4. 조달분류 우선순위

- 변경: `pubPrcrmntLrgClsfcNm` (대분류) > `pubPrcrmntMidClsfcNm` (중분류) 순으로 우선 사용
- 이전에는 중분류만 사용하여 대분류 정보가 누락되었음

### 4-5. fetch_detail() 변경

- g2b.go.kr 스크래핑 코드 전체 제거 (약 70줄)
- `bs4` (BeautifulSoup) import 제거 — 의존성 1개 감소
- `return None` 으로 단순화
- 이유: API 단건 조회 미지원 + 수집 시점에 이미 모든 가용 필드 저장

---

## 5. 실패한 접근과 원인

### 5-1. 비-PPSSrch API로 단건 조회 시도 (실패)

**시도한 것:**
1. `DETAIL_SERVICES` 매핑 딕셔너리 구성 (용역/물품/공사별 비-PPSSrch operation명)
2. `fetch_detail()`에서 bidNtceNo로 비-PPSSrch API 호출 로직 구현
3. 테스트 실행

**결과:**
- 에러코드 08: "필수값 입력 오류" — `inqryBgnDt`, `inqryEndDt`, `inqryDiv` 미제공
- 필수값을 모두 제공하면 bidNtceNo를 무시하고 전체 결과(23,034건) 반환
- 단건 필터링 불가 확인

**교훈:** data.go.kr API 파라미터가 "선택"으로 표시되어도, 실제로는 무시될 수 있다. 반드시 실제 호출로 검증해야 한다.

### 5-2. 비-PPSSrch의 bidNtceNo 필터 기대 (잘못된 가정)

처음에는 비-PPSSrch operation이 PPSSrch보다 유연한 필터를 제공할 것으로 가정했다. 실제로는 반대로, 비-PPSSrch는 날짜 범위 기반 조회만 지원하며 개별 공고 번호 필터를 지원하지 않는다.

---

## 6. 향후 주의점

1. **나라장터 사업개요(content)는 현재 어떤 방법으로도 가져올 수 없음** — API에 필드 없음, g2b.go.kr 스크래핑 차단
2. **contact_email은 PPSSrch API 응답에 포함되지 않음** — extra에 키는 존재하지만 값은 항상 빈 문자열. 향후 비-PPSSrch로 전환하면 확보 가능
3. **bidwatch에서 `SKIP_DETAIL_TYPES = {"nara"}` 유지가 적절** — fetch_detail이 None을 반환하므로 불필요한 호출 방지
4. **tech_eval_ratio, price_eval_ratio는 40.3% 커버리지** — 해당 필드가 있는 공고만 평가 비율 정보를 가짐 (협상에 의한 계약 방식일 때 주로 존재)
5. **API 제공 bidNtceDtlUrl 사용 중** — g2b.go.kr URL 구성 방식에 의존하지 않으므로 URL 체계 변경에 강건함

---

## 7. lets_portal과의 차이 (참고)

이번 변경으로 bid-collectors의 나라장터 필드가 lets_portal과 거의 동일해졌으나, 아래 차이가 남아 있다:

| 항목 | lets_portal | bid-collectors | 상태 |
|------|-------------|---------------|------|
| 사업개요 (content) | g2b.go.kr 스크래핑 | 불가 (차단) | 미해결 |
| 담당자 이메일 | 비-PPSSrch API | PPSSrch API (미제공) | 코드만 존재 |
| 첨부파일 | 규격서 포함 | 규격서 포함 (동일) | 해결 |
| 낙찰방식 | extra 저장 | extra 저장 (동일) | 해결 |
| 평가비율 | 미수집 | extra 저장 | bid-collectors가 우위 |
