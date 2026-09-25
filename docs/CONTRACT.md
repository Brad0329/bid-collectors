# bid-collectors 소비자 계약 — 변경 결정 기록

> 이 패키지에는 DB 스키마가 없다. 템플릿의 '데이터 모델/스키마 변경 게이트'는 **소비자 계약**에 건다 —
> 되돌리기 비싼 것은 테이블이 아니라 **BidWatch가 기대하는 모양**이기 때문이다(2026-09-23 이식 시 결정).
>
> **계약의 범위**: `Notice` · `CollectResult` · `BaseCollector`(`collect`·`health_check`·`fetch_detail`·생성자 키 규칙) ·
> `ScraperConfig`(BidWatch의 AI가 이 스키마대로 config를 만든다) · 공개 export(`bid_collectors/__init__.py`의 `__all__`) ·
> 수집기별 `bid_no` 형식(BidWatch가 `(source, bid_no)`로 upsert한다 — 형식이 바뀌면 같은 공고가 중복 저장된다).
>
> **변경 절차**: ① 변경안(무엇을·왜·영향 범위·버전)을 아래 '변경 이력'에 먼저 기록 → ② 사용자 확인 →
> ③ 코드 반영 + `docs/interface.md` 갱신 **— 이 문서는 bidwatch 저장소에도 같은 파일이 있다. 여기서 고치고 `docs/handover/v<버전>.md`로
> 넘긴다. bidwatch 쪽 파일은 bidwatch 세션이 반영한다(2026-09-25 사용자 결정 — 이 저장소 세션은 bidwatch 폴더를 수정하지 않는다)**
> → ④ `pyproject.toml`·`bid_collectors/__init__.py`의 버전을 함께 올린다.
>
> **버전 규칙**: 필드·선택 인자 **추가**(기본값 있음) = minor / 필드 제거·이름 변경·타입 변경·bid_no 형식 변경 = **major**.
> `extra` 안의 키는 계약 밖이다(수집기가 자유롭게 넣고 BidWatch는 JSONB로 통째 저장).
>
> 이 문서는 "결정과 이유"를, 코드(`bid_collectors/models.py`·`base.py`·`generic_scraper.py`)는 "현재 상태"를 담당한다.
> 둘이 어긋나면 코드가 정답이고 이 문서와 `interface.md`를 고친다.

## 설계 원칙
- **숨기지도 더하지도 않는다 — 최상위 원칙 (2026-09-25 사용자 확정)**: 출처가 주는 것은 전부 가져와 정확히 전달되는 형식으로
  떨어뜨리고, 무엇을 쓸지는 소비자(BidWatch)가 정한다. 근거 ① 안 받은 것은 되돌릴 수 없다 — API는 최근 기간만 주므로 지금 버린
  값은 과거 공고분을 다시 받을 수 없다 ② 골라 담는 손 매핑은 반드시 틀린다 — 2026-09-25 실측(최근 3일 1페이지 100건 표본): 나라장터
  응답 태그 중 용역 113개의 41개·물품 101의 40·공사 143의 37만 읽고, 이름이 틀려 항상 빈값인 키 2개(`cntrctMthdNm`→실제
  `cntrctCnclsMthdNm`, `bidQlftcRgstDt`→`bidQlfctRgstDt`), 취소공고(`ntceKindNm`)가 `ongoing`으로 나간다.
  - **표준 필드(`Notice`)는 출처가 준 값을 통일된 타입으로만 담는다.** 형식 통일(날짜·정수·절대 URL)은 하되 추정(마감일로 status 판정)·
    대체(배정예산 없으면 추정가격)·합성(대분류 > 중분류)·절단(content 500자)·상수(기관명 고정)·기본 필터(모집중만)는 하지 않는다.
  - **`extra`에는 응답 항목의 비어 있지 않은 필드 전부를 원래 이름 그대로** 담는다. 이름을 바꾸거나 골라 담지 않으며, 표준 필드로 옮긴
    값도 원문 그대로 포함한다(제외 목록 역시 손 매핑이라 두지 않는다).
  - 이 패키지에 남는 것은 "숨기지 않기"의 다른 얼굴이다 — 페이지를 끝까지 넘기기, 실패·절단을 `errors`로 알리기, 항목을 조용히
    버리거나 합치지 않기, 출처별 ID를 `bid_no`로 고정하기, 타입 통일.
  - **적용 트랙**: `extra` 원문 전부는 계약 밖이라 저위험 / 표준 필드의 파생 제거와 `status` 의미 변경은 BidWatch가 받는 값이 바뀌므로
    일반 트랙 — 변경안을 아래 이력에 먼저 적고 확인받는다. 현재 위반 목록과 수용 기준은 `docs/REQUIREMENTS.md` '공통 계약'.
- 수집기는 **가져오기만** 한다 — 저장·매칭·스케줄·캐시는 소비자. 새 요구가 소비자 쪽 일이면 여기서 받지 않는다.
- 동기 대신 **async(httpx)** — BidWatch 워커가 여러 출처를 병렬로 부른다.
- 부분 실패는 예외가 아니라 `CollectResult.errors`/`is_partial`로 알린다 — 앞서 모은 공고를 버리지 않는다
  (v1.1.0에서 `collect()` 경로 전부 적용. 예외: 나라장터 확장 3메서드는 반환형이 list라 예외로 알린다 — 아래 결정).
- 출처별 추가 필드는 `Notice`에 필드를 늘리지 않고 `extra`에 둔다(출처마다 필드가 달라 표준화 비용이 크다).

## 결정
### `_fetch()` 템플릿 메서드 — 서브클래스는 `collect()`를 오버라이드하지 않는다
- **결정**: `_fetch()`가 `(notices, pages_processed)`를 반환하고 `collect()`가 중복 제거·계측·예외 포착을 한다.
- **이유**: 중복 제거·에러 래핑이 수집기마다 달라지는 것을 막는다. kwargs에 페이지 수를 싣던 초기 설계는
  kwargs가 복사돼 동작하지 않았다(`work_log/Phase_002.md` §9-5).

### 나라장터 확장 메서드는 `list[Notice]`를 반환한다
- **결정**: `collect_awards`·`collect_contracts`·`collect_pre_specs`는 `CollectResult`가 아니라 리스트를 반환(2026-04-11).
- **결과**: 에러가 예외로 호출자에게 간다. 바꾸면(→ `CollectResult`) BidWatch 연쇄 수집 코드가 깨지므로 **major**.
  v1.1.0에서 재시도 소진도 조용한 빈 결과 대신 `RuntimeError`(키 마스킹)로 바꿨다 — 부분 결과 보존은 반환형을 바꿔야 해서 안 함(2026-09-23 사용자 확정).

### 수집기 내부 오류는 `_fetch`의 세 번째 반환 요소로 올린다 (2026-09-23)
- **결정**: `_fetch`가 `(notices, pages, errors)`를 돌려줄 수 있고 `collect()`가 errors를 마스킹해 싣고 `is_partial = bool(errors)`.
  2-튜플도 계속 받는다.
- **버린 대안**: 인스턴스에 누적(`self._errors`) — `_fetch` 시그니처는 안 바뀌지만 같은 인스턴스로 `collect()`를 동시에 부르면 오류가 섞인다.

### 나라장터 `fetch_detail`은 None
- **결정**: data.go.kr에 단건 조회·사업개요 API가 없어 g2b 스크래핑을 제거하고 None 반환, 상세 필드는 수집 시 extra에(2026-04-13).
- **근거**: `work_log/Phase_003_detail.md`.

## 변경 이력 (최신이 위)
| 날짜 | 변경안 (무엇을, 왜, 영향 범위, 버전) | 사용자 확인 | 반영 |
|---|---|---|---|
| 2026-09-25 | **v1.2.5(예정, Phase 006)** — 원칙 ① 적용: `extra`를 손 매핑(영어 키) 대신 응답 항목의 비어 있지 않은 필드 전부·원래 이름으로(0·False 포함, None·빈 문자열 제외, XML 반복 태그는 list, 값은 원문 그대로). 요청 문맥 `bid_type`·`data_type`은 응답에 없는 값이라 뺀다(`bid_no` 접두사에 있다). 이유: 설계 원칙 첫 항목. 영향: `extra`는 계약 밖이지만 BidWatch `NoticeModal.tsx`가 영어 키 26개를 읽으므로 그쪽 수정이 같은 시기에 필요(plan.md Phase 006 4항 대응표), 기존 DB 행은 오픈 전 재수집으로 교체. `Notice`·`CollectResult`·시그니처·bid_no 불변 → patch. 함께: 나라장터 확장 3메서드 빈 ID 병합 방지(A, 경고 로그), 항목 try 밖 계산의 전체 손실 제거(B). `interface.md` §1 `extra` 설명 갱신(여기 — bidwatch 반영은 handover) | ✅ 2026-09-25 (기본안 6건 — "작업 완료하고 대기" 지시로 확정) | 2026-09-25 Phase 006 커밋(해시는 `docs/handover/v1.2.5.md`) |
| 2026-09-24 | v1.2.0 — `AlioCollector` export 추가(알리오 공공기관 입찰공고, 공개 JSON `GET alio.go.kr/occasional/findBidList.json`, **API 키 불필요** — `GenericScraper`처럼 `__init__`에서 키 검사를 건너뛴다). `source="알리오"`, **`bid_no="ALIO-{seq}"`**, organization=공고 기관(`pname`). 이유: 자체조달 공기업(수자원·코레일·한전·LH…) 공고가 나라장터 API에 없고 알리오에 모인다(bidwatch `docs/procurement_sources_research.md` 3-1, 최근 100건 중 42건 나라장터에 없음). 영향: 추가만 — 기존 수집기·모델 불변. minor | ✅ 2026-09-24 (bidwatch 세션 — "전용 수집기 + 공공 출처" 선택) | |
| 2026-09-23 | v1.1.0 신뢰성 — 페이지 실패·쿼터 초과·max_pages 절단을 `errors`/`is_partial`로 보고(기존 필드, 의미만 채움. errors 문자열의 API 키는 마스킹) · `GenericScraper(config, event_hooks=None)` 선택 인자 추가(httpx `event_hooks` 형식 그대로, session_init_url·페이지·health_check·리다이렉트 전부에 걸린다. `create_client`는 원래 `**kwargs`로 넘기던 것을 문서화) · 나라장터 확장 3메서드는 반환형 유지 — 재시도 소진 시 조용히 빈 결과 대신 예외(키 마스킹) · 내부: `_fetch`가 `(notices, pages, errors)` 3-튜플을 돌려줄 수 있다(2-튜플도 계속 받음, BidWatch는 상속하지 않음) — minor | ✅ 2026-09-23 (bidwatch 세션 합의 + 이 세션 확정) | `5af39f3` (bidwatch `67fe562` interface.md) |
| 2026-04-13 | 나라장터 `fetch_detail` 스크래핑 제거 → None | ✅ | `1a037e3` |
| 2026-04-11 | v1.0.0 — `GenericScraper`/`ScraperConfig` export, 나라장터 확장 3메서드, `fetch_detail` 추가 | ✅ | `3f21d98`·`714420e` |
| 2026-04-06 | 초기 계약 — `Notice`·`CollectResult`·`BaseCollector._fetch` 템플릿 메서드 | ✅ | `c61291e`·`c188559` |

## 알려진 문서 불일치
(없음 — 2026-09-23 v1.1.0에서 정리: `fetch_detail`·`ScraperConfig`·`ScraperConfig | dict` 기재, 미구현 수집기 환경변수 행 제거,
bidwatch 쪽 판을 이쪽과 동일하게 맞춤 `67fe562`.)
