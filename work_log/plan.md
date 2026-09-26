# bid-collectors 전체 계획 (단일 원본)

> PLAN MODE에서 최초 작성/주요 변경 시 갱신. Phase 완료 시 메인 agent가 체크박스 `[x]` + 완료일.
> `Phase_XXX.md`는 실패한 접근·버린 대안이 있을 때만 만든다.
> 2026-09-23 기존 `docs/plan.md`에서 이관(greenfield 템플릿 체계 이식). **번호 대응: 구 "Phase N" = 새 "Phase 00(N+1)"**
> (구 Phase 0 → 001, 1 → 002, 2 → 003). 옛 로그 본문의 "Phase N"은 구 번호다 — 로그는 내용 불변으로 옮겼다.
> **"Phase 3"의 두 뜻 정리**: 구 plan.md의 "Phase 3"은 공기업 API 5종, `Phase_003.md`(구 phase2.md) §8·§13과
> `docs/generic_scraper.md` §8의 "Phase 3"은 JSON API 모드였다. 둘 다 번호를 떼고 아래 '이후 단계'로 옮겼다
> (JSON API 모드 = v1.2, 공기업 API = 필요성 판단 후).

## 시스템 개요
- 공공기관 입찰공고·지원사업 공고를 공공 API(나라장터·K-Startup·기업마당·보조금24·중소벤처기업부)와 임의 HTML
  게시판(GenericScraper)에서 가져와 표준 `Notice`/`CollectResult`로 돌려주는 파이썬 패키지(v1.3.0).
  소비자는 BidWatch(`C:\Users\user\Documents\bidwatch`, editable 설치). 역할 경계: 외부 사이트에서 공고를 가져오는 것은
  전부 이 패키지 / DB 저장·키워드 매칭·스케줄링·AI 설정 생성·캐싱은 BidWatch. 요구사항 상세는 `docs/REQUIREMENTS.md`.

## 아키텍처
- Python 3.11+ · httpx(async) · Pydantic v2 · lxml · BeautifulSoup4. 테스트 pytest + pytest-asyncio + respx.
- `BaseCollector._fetch()` 템플릿 메서드 → `collect()`가 중복 제거·계측·예외 포착 → `CollectResult`.
- 공개 계약 `docs/interface.md`(bidwatch에도 같은 문서) · 계약 변경 결정 `docs/CONTRACT.md` ·
  설계 상세 `docs/bid_collectors.md`·`docs/generic_scraper.md` · lets_portal 원본 레퍼런스 `docs/dev_reference.md`.

## Phase 체크리스트

> **가장 위험한 것을 먼저 한다.** "위험한 것" = 틀리면 되돌리기 비싼 것, 가능한지 아직 모르는 것.

- [x] Phase 001: 프로젝트 셋업 — Notice·CollectResult·BaseCollector·유틸 이식 (2026-04-06) — 로그 `Phase_001.md`
- [x] Phase 002: MVP 수집기 5종 — 나라장터·기업마당·보조금24·K-Startup·중소벤처기업부 (2026-04-06) — 로그 `Phase_002.md`, 코드 리뷰 수정 `Phase_002_review.md`
- [x] Phase 003: GenericScraper + 나라장터 확장(낙찰·계약·사전규격) + fetch_detail + v1.0.0 릴리스 (2026-04-11, 나라장터 상세 필드 보강 2026-04-13) — 로그 `Phase_003.md`(구현 상세) · `Phase_003_decisions.md`(결정 이유) · `Phase_003_detail.md`(나라장터 상세 API 조사·실패한 접근)
  - 구 완료 기준 대조(2026-09-23 이식 시): 나라장터 확장·v1.0 릴리스·README 완료 / 중소벤처24는 **안 함**(LINK 타입 API, `smes.py`가 같은 데이터 — `Phase_003.md` §10) /
    "lets_portal config 샘플 5개 통과"는 체크되지 않았으나 BidWatch가 2026-09-23 손 설정 39곳을 GenericScraper로 돌려 기준선으로 씀(29곳 1건 이상 — bidwatch `work_log/plan.md`)
- [x] Phase 004: v1.1 신뢰성 — 조용한 실패·절단 제거 + 요청 검사 훅 (F-001·F-005·F-007 미충족 기준) — **interface 호환(기존 필드 사용·선택 인자 추가) → minor, 1.0.0 → 1.1.0**
  (2026-09-23, `5af39f3`·`3ff4547`·`b35b3dd`, 태그 v1.0.0=`a2ce40c`·v1.1.0 — 로그 `Phase_004.md`. qa-tester 352 passed/0 failed(실 API 17건 포함), BidWatch backend/tests 통과)
  - 출처: 2026-09-23 bidwatch 세션 합의 작업 지시서. 착수 전 `docs/CONTRACT.md` 변경 이력의 예정 행을 사용자 확인으로 확정.
  1. **조용한 실패 제거** — 페이지 요청 실패 시 로그만 남기고 `break`하는 곳을 `CollectResult.errors`/`is_partial`에 기록:
     `generic_scraper.py` ~200 · `bizinfo.py` ~55 · `kstartup.py` ~54 · `subsidy24.py` ~59(+ ~63 API 에러 코드) · `smes.py` ~55(+ ~61 XML 에러) ·
     `nara.py` ~348·~384(`_request_with_retry`가 None을 돌려주면 `break`). GenericScraper는 1페이지 실패 시 0건 + errors 빈 리스트라
     소비자가 "사이트 장애"와 "공고 없음"을 구분 못 한다. `_fetch` 반환 모양을 바꿀지(예: 세 번째 요소 errors) 내부 누적으로 할지는 착수 시 결정
     — `_fetch`는 서브클래스 내부 계약이라 공개 계약은 안 바뀐다. 기존 `test_*_error_graceful`·`test_http_error_returns_empty`의 기대가 바뀐다.
  2. **나라장터 부분 결과 보존** — `resultCode != 00`(쿼터 초과 포함)이면 `_parse_xml_items`가 `ValueError` → `_fetch` 전체 중단 →
     앞서 수집한 서비스 유형 결과까지 버려짐(`base.py:47`). 앞 결과를 보존하고 errors에 기록. `_fetch_extended`(확장 3메서드)도 같은 구조 —
     단 반환형이 `list[Notice]`라 errors를 담을 곳이 없다(`docs/CONTRACT.md` 결정 참조, 바꾸면 major).
  3. **조용한 절단 보고** — `max_pages` 상한 도달 시 `is_partial` + errors(또는 메시지)로 "잘렸음"을 알린다. 대상: GenericScraper(기본 3),
     기업마당·K-Startup·보조금24·중소벤처기업부(기본 50). 나라장터는 상한이 없다(totalCount까지).
  4. **낡은 테스트 27개** — 고정 날짜 픽스처(2026-04-01~30)를 실행 시점 기준 상대 날짜로. 기업마당 9·K-Startup 17·보조금24 1건
     (`test_bizinfo.py`·`test_kstartup.py`·`test_subsidy24.py`의 `SAMPLE_ITEM`). 이식 시점 기준선 **27 failed / 292 passed**.
  5. **요청 검사 훅 주입** — `GenericScraper`(와 `create_client`)에 httpx request event hook(또는 transport)을 주입하는 선택 인자.
     BidWatch가 SSRF 방어 훅(bidwatch `backend/app/services/url_guard.py`의 `guard_request`)을 정기·시험 수집에도 걸기 위함 —
     지금은 리다이렉트(`follow_redirects=True`)로 내부망에 갈 수 있는 bidwatch 보류 위험. `session_init_url` 요청에도 걸려야 한다.
  6. (함께) 보조금24 cutoff 시각 절삭 누락(`subsidy24.py:38` — 다른 수집기는 자정 절삭) · 버전 1.0.0 → 1.1.0(`pyproject.toml`·`__init__.py`) ·
     `docs/interface.md` 갱신 **양쪽 저장소**(`docs/CONTRACT.md` '알려진 문서 불일치'도 함께 정리)
  7. (항목 1과 한 몸) **errors에 API 키를 싣지 않는다** — httpx `raise_for_status()`의 예외 문자열에 쿼리스트링(`serviceKey=…`)을 포함한
     전체 URL이 들어간다. 지금도 `health_check()`가 `message: str(e)`로 키를 돌려줄 수 있고, 항목 1로 페이지 오류를 errors에 담으면
     `CollectResult.errors` → BidWatch 수집 이력 DB까지 키가 간다. 기록 전 키 마스킹 + 그것을 잡는 테스트.
  - 완료 조건: 전체 테스트 0 failed · REQUIREMENTS F-001·F-005·F-007의 미충족 기준 충족 + 테스트 대응 · BidWatch 쪽 연동 확인은 bidwatch 세션 몫
- [x] Phase 005: 항목 수준 조용한 실패 제거 — 필드 이상 1건이 결과를 지우거나 조용히 사라지지 않게 (F-001·F-003~F-007) — **계약 불변(errors 내용만 늘어남) → patch**
  (2026-09-25, v1.2.4 — 로그 없음(실패한 접근 없음). qa-tester 400 passed/0 failed(실 API 18건 포함, 실데이터 건너뜀·셀렉터 불일치 0건), BidWatch backend/tests 118 passed)
  - 출처: 2026-09-24 구조 리뷰(rebuild 사유 판정 — 전면 재작성 불요, R1~R3만 지금). 재현 스크립트 `scripts/_tmp/review_repro.py`(고정 응답, 코드 수정 없음).
    Phase 004는 **페이지** 단위 실패를 맞췄고 **항목** 단위는 수집기마다 4갈래로 남았다 — 알리오 v1.2.2가 한 곳만 고친 것이 "한쪽만 고쳐 재발"의 실례.
  - 트랙: 저위험. `BaseCollector`에 **비공개** 헬퍼만 추가하고 `_fetch` 시그니처·템플릿 구조·bid_no 형식은 그대로 — interface.md 변경 없음.
    단 BidWatch가 받는 `is_partial=True`가 늘어난다 → 완료 시 bidwatch `backend/tests` 통과 확인 + "사용자 실테스트 대기"에 한 줄.
  1. **R1 항목 변환 실패 정책 통일** — 한 항목의 예외는 그 항목만 건너뛰고 **사유별 건수**를 errors에(알리오 `skip_reasons` 방식).
     재현: 기업마당(`bizinfo.py:72`)·보조금24(`subsidy24.py:85`)는 try가 없어 null 필드 1건에 **앞 페이지까지 0건**(pydantic 원문이 errors로) /
     나라장터(`nara.py:378`)·중소벤처(`smes.py:80`)는 로그만 남기고 버림 / 보조금24 `subsidy24.py:130`은 None 반환으로 조용히 버림 /
     K-Startup(`kstartup.py:70`)은 try 없음 — `clean_html`이 None을 흡수해 지금은 버틸 뿐. 알리오·GenericScraper는 이미 보고한다(헬퍼로 옮길지는 착수 시).
  2. **R2 빈 ID 충돌** — 수집기별 필수 필드(최소 ID·제목)를 선언하고 빠지면 R1 경로로 건너뛰고 보고
     (errors 형식 확정 2026-09-24 사용자: 항목별 줄이 아니라 사유별 한 줄 + 건수, 알리오 방식). 재현: `pblancId` 없는 3건 → `"BIZINFO-"`로 합쳐져 1건,
     errors 없음(BidWatch upsert에서 서로 다른 공고가 한 행을 덮어씀). 같은 자리: `kstartup.py:188`·`smes.py:139`·`nara.py:103`(입찰공고). 숫자 ID 0은 유효(알리오 교훈).
  3. **R3 GenericScraper 셀렉터 불일치 감지** — 목록 행은 잡혔는데 공고 0건·cutoff 이전 행 0건이면 "셀렉터 불일치 의심 — 행 N개 중 추출 0건"을 errors에.
     재현: 제목·날짜 셀렉터가 틀리면 0건·errors 없음(`generic_scraper.py:359-372`의 continue는 세지 않음). 실례: bidwatch plan.md:100 손 설정 3곳이
     사이트 개편으로 낡은 것을 기준선 대조로만 발견 / bidwatch는 시험 수집에만 "날짜 파싱 50% 미만" 탈락을 두고 정기 수집엔 없다.
     **확정(2026-09-24 사용자)**: 목록 행 자체가 0개인 경우는 보고하지 않는다(빈 게시판과 구분 불가).
  4. **(debt-audit 흡수) R3와 한 몸 — GenericScraper 멈춤 조건의 정렬 가정** — `generic_scraper.py:243`은 한 페이지에 기준일 이내 0건이면 멈춘다
     (옛 알리오의 임계 1과 같은 유형 — 5afbc99는 이 가정으로 472건을 조용히 놓쳤다). 셀렉터 불일치 0건도 같은 조건으로 멈추므로 R3가 둘을 구분해야 한다.
     **확정(2026-09-24 사용자)**: 임계는 1 유지(2연속은 요청 수 = delay × BidWatch 사이트 39곳이 늘어난다) — 셀렉터 불일치와 오래된 페이지의 구분만 한다. 회귀 테스트는 뒤섞인 순서 픽스처(`test_alio::test_interleaved_old_item_does_not_stop_collection` 방식).
  5. **(debt-audit 흡수) 헛통과 테스트** — `tests/test_generic_scraper.py:377,386`은 고정 날짜(2026-04-10)라 cutoff에 걸려 셀렉터·제목과 무관하게 0건으로 통과한다 —
     상대 날짜로 바꾸고 변이 확인. R3와 같은 파일·경로.
  6. **(debt-audit 흡수) 공통 계약 테스트** — R1·R2 완료 조건을 수집기별 복사 대신 `__all__`의 모든 수집기에 자동으로 도는 테스트로
     (null 필드 1건·빈 ID·숫자 0 보존 — 0 오판 3회·한 수집기만 고침 3회 이상의 구조 강제). 새 수집기가 자동 편입돼야 한다.
  - 범위 밖(이유): `_fetch_extended`(낙찰·계약·사전규격)의 항목 건너뜀은 반환형 `list[Notice]`라 errors 채널이 없다(CONTRACT.md) — 로그만 유지, 바꾸면 major.
  - 완료 조건: REQUIREMENTS '공통 계약'의 (v1.2.4) 기준 3개 충족 + 수집기별 "null 필드 1건·빈 ID"에서 **건수를 재는** 테스트(변이 확인) ·
    전체 테스트 0 failed · 버전 1.2.3 → 1.2.4(`pyproject.toml`·`__init__.py` — 1.2.3은 알리오 누락 수정 5afbc99가 썼다)
- [x] Phase 006: v1.2.5 — 항목 잔여 결함 A·B + 원칙 ① `extra` 원문 전부 전달 (F-001~F-006·F-010, F-002 확장 3메서드) — **계약 불변(`extra`는 계약 밖, 시그니처·bid_no 불변) → patch 1.2.4 → 1.2.5**
  (2026-09-25, 구현 `a5ceaa0` — 로그 없음(실패한 접근 없음). handover `docs/handover/v1.2.5.md` — 단위 421 passed·ruff 통과 / 실호출 extra 실측: 나라장터 3서비스 1페이지 300건·알리오 10건 전부
  `extra == 비어 있지 않은 필드` / 변이 14건 전부 잡힘(`scripts/_tmp/mutate_phase006.py`) / BidWatch `backend/tests` 118 passed(읽기·실행만) /
  qa-tester 합격: 441 passed/0 failed(실호출 20건 포함, 통합 errors는 절단 보고뿐·항목 건너뜀 0건) + 추가 실측 낙찰 용역 7일 155건·사전규격 1일 2건 —
  bid_no 전부 고유, `-`로 끝나는 ID 0, extra 전부 원문 태그. 완료 조건 전부 충족 — BidWatch 반영은 handover §5에서 확인)
  - 출처: 2026-09-25 세션 평가(Phase 005 잔여 A·B) + 같은 날 사용자 확정 최상위 원칙(`docs/CONTRACT.md` 설계 원칙 첫 항목, 발급 2026-09-25).
    원칙 ②(표준 필드 파생 제거)는 이 Phase 범위 밖 — 소비자 값이 바뀌어 일반 트랙으로 따로 발급.
  - 트랙: 저위험. 단 BidWatch 화면이 `extra`의 영어 키를 읽고 있어(아래 4) 그쪽 후속이 같은 시기에 필요 → 완료 시 bidwatch `backend/tests` 통과(읽기·실행만) + handover 문서 `docs/handover/v1.2.5.md`(대응표 포함).
  - **착수 전 확인 6건 — 확정(기본안 그대로, 2026-09-25 "handover 작성 직전까지 완료하고 대기" 지시)**:
    ① BidWatch가 읽는 영어 키 26개(4 참조) → **기본안: 원문 이름만 넘긴다(두 이름 병기는 원칙 위반). BidWatch `NoticeModal.tsx`가 원문 이름을 읽도록 고치고, 기존 DB 행은 오픈 전 1개월 재수집(upsert)으로 교체.**
    ② 요청 문맥 `bid_type`·`data_type`(응답에 없는 값) → **기본안: `extra`에서 뺀다 — `bid_no` 접두사(`용역-`·`낙찰-용역-`)에 이미 있다.** BidWatch의 "입찰 구분" 표시는 bid_no에서 자른다.
    ③ "비어 있지 않은"의 정의 → **기본안: None·빈 문자열·공백만 제외, 0·False·"0"은 포함.**
    ④ 형태 → **기본안: XML 반복 태그(중소벤처 `fileName`×N)는 list, 자식이 있는 태그는 dict, 값은 텍스트 그대로(숫자 변환 없음) / JSON은 값 타입·중첩 그대로.**
    ⑤ HTML이 든 값(K-Startup `aply_trgt_ctnt` 등, 지금은 clean_html 적용) → **기본안: 원문 그대로 — 표시용 정리는 BidWatch 몫.**
    ⑥ `docs/interface.md` §1 `extra` 설명·예시 갱신(여기서만 — bidwatch 쪽 반영은 handover로, 머리말 "양쪽 모두 업데이트"도 그때 고친다) + CONTRACT.md 변경 이력 예정 행 확정.
  1. **A — 나라장터 확장 3메서드 빈 ID 병합 방지**: `_award_item_to_notice`(`bidNtceNo`·`bidNtceNm`)·`_contract_item_to_notice`(`dcsnCntrctNo`|`untyCntrctNo`·`cntrctNm`)·
     `_prespec_item_to_notice`(`bfSpecRgstNo`|`refNo`)에 `require_fields`. `_fetch_extended`(nara.py:522)는 반환형이 list라 errors 채널이 없다 → `_record_skip`으로 세고
     끝에 `_skip_message`를 **경고 로그**로(보고 채널 추가 = 선택 인자 = minor, 보류에 기록). 테스트: ID 없는 3건 → 0건(합쳐진 1건이 아님) + caplog에 "3건".
  2. **B — 항목 하나의 값 이상이 결과 전체를 지우는 경로 제거**: 기업마당 `_is_within_cutoff`(bizinfo.py:126, 호출은 :87 `items[-3:]` — 항목 try 밖)·보조금24
     `_is_business_target`(subsidy24.py:182)를 어떤 값에도 예외 없이(비문자열은 "값 없음"으로) / 표준 필드 읽기의 null 안전 — bizinfo.py:155 `reqstBeginEndDe`가 null이면
     `"~" in None` TypeError로 ID·제목이 멀쩡한 공고가 "TypeError 1건"으로 버려진다(kstartup·subsidy24의 같은 자리 점검). 테스트: 공통 계약 테스트에 `optional_null` 케이스(수집기별) + 두 함수 단위 테스트(None·int·list).
  3. **원칙 ① — `extra` 원문 전부**: `base.py`에 공통 헬퍼(가칭 `raw_fields(item) -> dict | None`, XML `etree._Element`·JSON dict 모두, 규칙 ③④). 8곳(`nara._item_to_notice`·확장 변환기 3개·
     bizinfo·kstartup·smes·subsidy24·alio)의 손 매핑 extra를 헬퍼 호출로 교체(알리오는 지금 extra=None). 나라장터 오타 2개(`cntrctMthdNm`·`bidQlftcRgstDt`)와 공고첨부 태그 20개 루프는
     헬퍼가 대체하므로 함께 사라진다(attachments 표준 필드의 `ntceSpecDocUrl` 루프는 유지). 테스트: 공통 계약 테스트에 "모르는 필드가 든 항목 → extra 키 집합 == 비어 있지 않은 필드 집합,
     이름 바뀐 키 0개, 0 포함, 빈 값 제외"(수집기별 parametrize, XML 반복 태그 1건) / 통합: 나라장터 3서비스·나머지 4곳 1페이지에서 항목마다 `len(extra) == 비어 있지 않은 필드 수`(건수 기록).
     기존 테스트 기대 변경: extra 영어 키를 단언하는 테스트(nara `test_awards_mapping`·`test_contracts_mapping`·`test_pre_specs_mapping` 등) → 원문 이름.
  4. **BidWatch 후속(bidwatch 세션 몫 — handover 문서 `docs/handover/v1.2.5.md`로 전달, 여기서 bidwatch 파일은 수정하지 않는다)**: `frontend/src/components/notices/NoticeModal.tsx` NaraExtra·GeneralExtra 키 대응.
     나라장터: est_price→`presmptPrce`, budget→`asignBdgtAmt`, bid_method→`bidMethdNm`, contract_method→`cntrctCnclsMthdNm`(종전엔 오타로 항상 빈값), award_method→`sucsfbidMthdNm`,
     contact→`ntceInsttOfclNm`+`ntceInsttOfclTelNo`, contact_email→`ntceInsttOfclEmailAdrs`|`dminsttOfclEmailAdrs`, tech_eval_ratio→`techAbltEvlRt`, price_eval_ratio→`bidPrceEvlRt`,
     bid_qual→`bidQlfctRgstDt`(종전 오타), open_date→`opengDt`, bid_type→bid_no 접두사 / K-Startup: apply_method→`aply_mthd_onli_rcpt_istc`|`aply_mthd_vst_rcpt_istc`|`aply_mthd_etc_istc`,
     biz_name→`intg_pbanc_biz_nm`, biz_year→`biz_enyy`, contact→`prch_cnpl_no`, department→`biz_prch_dprt_nm`, excl_target→`aply_excl_trgt_ctnt`, target→`aply_trgt_ctnt`, target_age→`biz_trgt_age` /
     기업마당: reference→`refrncNm`, req_method→`reqstMthPapersCn`, sub_category→`pldirSportRealmMlsfcCodeNm`, target→`trgetNm`, view_count→`inqireCo`, hashtags 동일.
     확장 3메서드·보조금24·중소벤처 키는 읽는 곳 없음(2026-09-25 grep). `backend/app/services/notice.py:27` fetch_detail 병합은 F-008 영어 키 그대로(이번 범위 밖).
  5. **버전** 1.2.4 → 1.2.5(`pyproject.toml`·`__init__.py`) · CONTRACT.md 변경 이력 행 반영 칸 · interface.md §1(여기) · handover 문서 작성 · CLAUDE.md:3·plan.md:13의 "v1.1.0" 표기를 현재 버전으로.
  - 범위 밖(이유): 원칙 ②(status 추정·budget 대체·category 합성·content 절단·organization 상수·only_ongoing 기본값 — 소비자 값이 바뀌어 일반 트랙) / 공사 배정예산 `bdgtAmt`(budget 표준 필드 = 원칙 ②) /
    GenericScraper의 원문 전부(HTML 행에는 필드 이름이 없다 — 별도 설계, 보류) / `fetch_detail` 반환 dict의 원문화(F-008, BidWatch 병합 로직과 함께) / `_fetch_extended` 보고 채널(선택 인자 = minor).
  - 완료 조건: REQUIREMENTS 원칙 ① `[x]` + F-002 A 기준 + 공통 계약 B 기준 각각 테스트 대응(변이 확인) · 전체 테스트 0 failed · 실측(위 3의 통합, 표본 수 기록) ·
    BidWatch `backend/tests` 통과(읽기·실행만) · handover 문서 `docs/handover/v1.2.5.md`(4의 대응표·interface.md diff·검증법·되돌리기) +
    "사용자 실테스트 대기"에 링크 한 줄 · 버전 1.2.5.
- [x] Phase 007: v1.3.0 — 알리오 `fetch_detail` (F-010·F-008) — **기능 추가(알리오 반환 None → dict, 시그니처·bid_no 불변) → minor 1.2.5 → 1.3.0**
  (2026-09-25 — 로그 없음(실패한 접근 없음). handover `docs/handover/v1.3.0.md` — 단위 435 passed·ruff 통과 / 변이 9건 전부 잡힘(`scripts/_tmp/mutate_phase007.py`) /
  qa-tester 합격 457 passed/0 failed(실호출 22건) + 실측 목록 3페이지 30건: 키 집합·첨부 건수 30/30 일치, 예외 0, 첨부 83개, refrUrl 30(g2b 20·onbid 10), content 비어 있지 않음 0 /
  고장 5건(없는 seq 3·형식 2) 전부 예외 / BidWatch `backend/tests` 126 passed(읽기·실행만))
  - 출처: bidwatch 요청서 `bidwatch/docs/requests/bid-collectors_alio_fetch_detail.md`(2026-09-25, 팝업의 첨부·원문 링크·나라장터 연결).
    CONTRACT.md 변경안 확정 2026-09-25 사용자: 실패 = 예외 · `bFiles` 포함(원문 전부) · 1.3.0.
  - 트랙: 일반(interface.md §2 변경 — 계약 게이트). 검증: 전체 테스트 + BidWatch `backend/tests`(읽기·실행만) + 되돌리기 경로(태그·커밋).
  1. `AlioCollector.fetch_detail` + 단위 테스트(F-010 v1.3.0 기준 5개, 변이 확인) + 실호출 1건
  2. `interface.md` §2 · README 지원 현황 · 버전 1.3.0 · handover `docs/handover/v1.3.0.md` · "사용자 실테스트 대기"에 한 줄
  - 범위 밖: 요청서 §4 결함·불일치 10건(bidwatch `docs/source_fields.md` 부록 A) — 별도 발급.
  - 완료 조건: F-010 (v1.3.0) 기준 전부 `[x]` + 테스트 대응 · 전체 테스트 0 failed · 실측(표본 수 기록) · BidWatch `backend/tests` 통과 · handover.
- [x] Phase 008: 저위험 결함 5건 — bidwatch 필드 사전 부록 A #1·#3·#4·#9·#10 (F-002·F-001·F-006·F-003) — **계약 불변(표준 필드 값·bid_no·시그니처 불변) → patch 1.3.0 → 1.3.1**
  (2026-09-25 — 로그 없음(실패한 접근 없음, 착수 시 확인 3건 모두 "삭제/교체 대상 없음"). `d157609`·`17a044b` · handover `docs/handover/v1.3.1.md` —
  단위 438 passed·ruff 통과 / 변이 6건 전부 잡힘(`scripts/_tmp/mutate_phase008.py`) / 실측(`phase008_probe.py`): 계약 공사 1일 0 → 61건(건너뜀 61 → 0),
  제목 태그 용역 cntrctNm 100/100·물품 40/40·공사 cnstwkNm 61/61, K-Startup matchCount 230·totalCount 30,168 요청 4→3회 228건 동일, 잘못된 키 403 → errors·마스킹 /
  qa-tester 합격 460 passed/0 failed(실호출 22건) + 계약 공사 1일 62건(원문과 일치)·3일 10,572건 / BidWatch `backend/tests` 130 passed(읽기·실행만) /
  bidwatch 반영 확인 2026-09-25 — handover §5, 남은 것 없음)
  - 출처: bidwatch `docs/source_fields.md` 부록 A(2026-09-25 필드 사전 조사, 요청서 `docs/requests/bid-collectors_alio_fetch_detail.md` §4). 발급 2026-09-25 사용자.
    부록 A의 나머지 #2·#5·#6·#7·#8은 BidWatch가 받는 표준 필드 값(end_date·budget·region·organization·status)이 바뀌어 **일반 트랙** — 원칙 ② Phase에 합친다(이 Phase 범위 밖).
  - 트랙: 저위험. BidWatch가 받는 값이 바뀌는 것은 #1(쓰지 않는 확장 메서드 `collect_contracts`)뿐 → handover는 참고 수준 한 장.
  1. **#1 계약 공사 전건 누락** — `nara.py:209` `_contract_item_to_notice`가 제목을 `cntrctNm`으로 필수 검사하는데, 공사 응답의 제목 태그는 `cnstwkNm` → 공사 100/100건이 `require_fields`에서 건너뛰어진다(경고 로그만, 반환형 list라 errors 채널 없음).
     제목 = `cntrctNm` 없으면 `cnstwkNm`(업무별 제목 태그 — 파생 아님). 착수 시 용역·물품의 제목 태그도 실측으로 확인. `bid_no` `계약-{업무}-{dcsnCntrctNo|untyCntrctNo}`는 그대로.
     테스트: 공사 고정 응답(cnstwkNm만) N건 → N건 반환·제목 일치(변이 확인) / 실측: 계약 공사 1페이지 건수 ≠ 0, 건너뜀 경고 0.
  2. **#3 죽은 코드** — `nara.py:121-125` 공고첨부 루프가 `bidNtceFlNm{i}`/`bidNtceFlUrl{i}`를 읽는데 명세·실측(용역/물품/공사 1310/1108/942건)에 없는 태그. 삭제(규격서 `ntceSpecDocUrl` 루프는 유지).
     착수 시 명세에 공고 첨부에 해당하는 다른 태그가 있는지 확인 — 있으면 삭제가 아니라 원문 태그로 교체(그때 attachments 값이 늘어나므로 handover에 적는다).
  3. **#4 없는 원천** — `smes.py:158` budget ← `suptScale`/`supt_scale`: 명세·실측 모두 없는 태그라 budget 항상 None. 손 매핑 삭제(값 None 그대로 — 소비자 값 불변).
     착수 시 명세에서 지원 규모에 해당하는 태그가 있는지 확인 — 있으면 표준 필드 값이 생기는 변경이라 **멈추고 사용자에게 묻는다**(원칙 ② 영역).
  4. **#9 K-Startup 종료 판정** — `kstartup.py:69` `total_count = data.get("totalCount")`: 진행중 필터(`cond[rcrt_prgs_yn::EQ]=Y`)를 걸어도 전체 건수로 페이지 종료를 판정 → 헛호출 추정(**미실측**).
     먼저 실측(`totalCount`·`matchCount`·실제 받은 건수 비교, 표본 기록) → 맞으면 `matchCount`(보조금24 `subsidy24.py:84`와 같은 방식), 절단 보고 문구의 전체 건수도 그 값으로.
     debt-audit 보류의 "K-Startup odcloud `code<0` 미검사·`totalCount` 사용"을 흡수 — `code<0` 검사도 함께(보조금24 대조). 테스트: matchCount < totalCount 고정 응답에서 요청 수(변이 확인).
  5. **#10 문서 오기** — `docs/bid_collectors.md:26·107`의 `smes24.py`(중소벤처24 — Phase 003에서 **안 함**, `smes.py`가 같은 데이터)와 `docs/dev_reference.md:24` `mss_biz.py` → 현재 구조(`smes.py`)로. 원본 레퍼런스 성격이면 주석으로 대응만 표시.
  - 완료 조건: 1·4 테스트 대응(변이 확인)·#1·#9 실측(표본 수 기록) · 전체 테스트 0 failed · 버전 1.3.1(`pyproject.toml`·`__init__.py`) · handover `docs/handover/v1.3.1.md`(계약 공사 수집 재개 + K-Startup 요청 수 변화) · REQUIREMENTS F-002·F-003 기준 추가.

- [x] Phase 009: v1.4.0 — 자체조달 기관 수집기 4종 LH → 가스공사 → d2b → 수자원 (F-011~F-014) — **export·bid_no 추가 → minor 1.3.1 → 1.4.0**
  (2026-09-26 — 로그 `Phase_009.md`(d2b 차수·수자원 페이지 겹침·수의 시작일). `10ce7a4`·`23cdea0`·`1009bc0`·`9038c3c`·`873ced9` · handover `docs/handover/v1.4.0.md` —
  단위 518 passed·ruff 통과 / 변이 35건 전부 잡힘(`scripts/_tmp/mutate_phase009.py`) / qa-tester 합격 542 passed(실호출 포함) + 실측 4종 × days 7·14·30:
  합쳐진 행 0·bid_no 고유·잘못된 키 → errors·마스킹 / spec-checker 지적 3건 반영(빈 중간 페이지 보고·CONTRACT 수자원 category 문구·기준 테스트 이름) /
  BidWatch `backend/tests` 132 passed(읽기·실행만))
  - 출처: bidwatch 요청서 `bid-collectors_institution_collectors.md` ② 구현, 조사 `docs/institution_sources.md`, 계약 CONTRACT.md 2026-09-26 행(사용자 확인 2026-09-26).
  - 트랙: 저위험(새 수집기) — 단 interface.md 목록 갱신·새 bid_no가 있어 계약 게이트를 탔다. 검증: 기관별 단위(사다리) + 실호출 통합 + BidWatch `backend/tests`(읽기·실행만).
  1. `utils/datagokr.py` — data.go.kr XML 파싱(resultCode·빈 본문·EUC-KR) 공유 헬퍼
  2. LH · 3. 가스공사 · 4. d2b · 5. 수자원 — 기관마다 수집기 + 단위 테스트 + `test_item_contract` 편입 + 커밋
  6. 통합 테스트(실호출) · interface.md · README · 버전 1.4.0 · handover `docs/handover/v1.4.0.md`(기관별 1일 호출 수·금액 이름별 표시·알리오 연결 정규식)
  - 범위 밖: 수자원 사전규격·발주계획(공고번호 없음)·입찰결과, d2b 상세·품목명세, 한전 포털(키 미발급), 코레일(비공식)
  - 완료 조건: F-011~F-014 기준 `[x]` + 테스트 대응(변이 확인) · 전체 테스트 0 failed · 실측(표본 수) · BidWatch `backend/tests` 통과 · handover

- [x] Phase 010: v1.5.0 — 기관 수집기 4종 `fetch_detail` 수자원 → d2b → 가스공사 → LH (F-011~F-014·F-008) — **기능 추가(반환 None → dict, 시그니처·bid_no 불변) → minor 1.4.0 → 1.5.0**
  (2026-09-26 — 로그 없음(실패한 접근 없음 — 구현 중 발견 3건은 커밋 메시지: 가스 품목표 colspan·LH 같은 이름 표·spec-checker "첨부 조용히 []").
  `7587bae`·`c885f52`·`b3391a2`·`5c80f7a`·`05c1037`·`8631bd9`·마감 커밋 · handover `docs/handover/v1.5.0.md` — 마감 전체 606 passed(실호출 포함)·ruff 통과·tools_tests 149 passed /
  변이 48건 전부 잡힘(`scripts/_tmp/mutate_phase010.py`, 처음 안 잡힌 3건은 테스트 보강) / 실측 34건(수자원 8·d2b 10·가스 8·LH 8) + 저장 표본 가스 26·LH 23 파서 통과 +
  첨부 url 실제 GET 3기관 + 고장 6건 예외 / qa-tester 합격(601 passed, 새 표본 24건·고장 12건) / spec-checker 지적 반영(첨부 조용히 [] 방지·통합 테스트 독립 대조·문서 5건) /
  BidWatch `backend/tests` 134 passed(읽기·실행만))
  - 출처: bidwatch 요청서 `bidwatch/docs/requests/bid-collectors_institution_fetch_detail.md`(2026-09-26), 조사 2026-09-26 researcher 4건(원문 `scripts/_tmp/{kwater,kogas,lh,d2b}_dtl/`),
    계약 CONTRACT.md 2026-09-26 v1.5.0 행(사용자 확인 — d2b 3종 목록 조회 후 상세 · LH TLS 중간 인증서 동봉).
  - 트랙: 일반(interface.md §2 변경 — 계약 게이트). 검증: 기관별 단위(사다리) + 실호출 통합 + 전체 테스트 + BidWatch `backend/tests`(읽기·실행만) + 되돌리기(태그 v1.4.0).
  1. 수자원 · 2. d2b · 3. 가스공사 · 4. LH — 기관마다 `fetch_detail` + 단위 테스트(변이 확인) + 커밋
  5. 통합 테스트(실호출, 기관별 최근 공고 1건 이상) · interface.md §2 · README · 버전 1.5.0 · handover `docs/handover/v1.5.0.md`
     (d2b 호출 수·목록 한도 공유, LH 1.2GB 첨부 실례 — 스트리밍 권장, 비공식 3곳 깨짐 = 예외, 수자원 마감 `-` 공고는 상세 일정에 마감이 있다)
  - 범위 밖: 첨부 파일 내려받기(url만 준다) · 수자원 목록 마감일을 상세 일정으로 채우기(수집 시점 정규화 — bidwatch 요청서 §4, 원칙 ②)
  - 완료 조건: REQUIREMENTS F-011~F-014 (v1.5.0) 기준 `[x]` + 테스트 대응 · 전체 테스트 0 failed · 실측(표본 수) · BidWatch `backend/tests` 통과 · handover

## 이후 단계 (Phase 번호 미발급 — 착수 시 번호를 받고 위 체크리스트로 옮긴다)

- **원칙 ② Phase(일반 트랙)** — 표준 필드 파생 제거 + bidwatch 필드 사전 부록 A 남은 #2·#5·#6·#7·#8(end_date·budget·region·organization·status).
  **#6(공사 지역)·#8(취소)은 bidwatch가 이미 `extra` 원문으로 처리 중** — 표준 필드를 바꿀 때 handover에 바뀐 값·조건을 적어 bidwatch 로직과 맞춘다(2026-09-25 bidwatch 요청).
- **자체조달 기관 수집기** — 요청서 `bidwatch/docs/requests/bid-collectors_institution_collectors.md`(2026-09-25). ① 조사(기관별 실호출·인증·호출 수·알리오 연결 키) →
  사용자가 구현 기관 선택 → ② 구현. 조사 결과는 `docs/institution_sources.md`.

- **v1.2 JSON API 모드** — 설계 `docs/generic_scraper.md` §8-1(`api_mode="json"`). 그 뒤 전용 사이트:
  창조경제혁신센터 7개 지역 · 부산창업포탈 · 한국예탁결제원 · 창조경제혁신센터 지원사업(lets_portal 전용 수집기 — `docs/dev_reference.md` §8).
  같은 문서의 후보: §8-2 `bid_no_regex`(URL의 안정적 ID로 bid_no) · §8-3 상세 페이지 스크래핑.
- **공기업 API 5종 — 필요성 판단 후** (구 plan.md "Phase 3", data.go.kr 서비스 ID까지 조사 완료):
  LH 입찰공고 15021183(+ 계약 15021184·발주계획 15042795·사전규격 15042796·개찰 15057180) · 한전 전자입찰계약 15148223 ·
  도로공사 전자조달 계약공개 15128076 · 수자원공사 전자조달 입찰공고 15101635 · 방위사업청 입찰공고 15002040(+ 결과 15002018·
  조달계획 15002017·계약 15002019·코드 15002020). 나라장터에 안 올라오는 자체 입찰건 확보가 가치. 구현은 Phase 002 패턴 복제.
- **호출 한도 대응(종전 가칭 "v1.3" — 1.3.0은 알리오 fetch_detail이 썼다)** — `CollectResult`에 data.go.kr 호출 수 반환(일 1,000회 한도를 BidWatch가 합산하기 위함, 필드 추가 = minor) ·
  429 지수 백오프(현재 나라장터만 30초 고정 3회).
- **헤드리스 브라우저는 하지 않는다**(2026-09-23) — JS 렌더링 사이트는 JSON 모드·소비자 AI의 내부 JSON 감지가 우선.
- 구 plan.md "Phase 4 품질+운영"의 나머지: 로깅 표준화 · ~~수집기별 필드 매핑표 문서~~(**안 함** 2026-09-25 — 원문 전부 전달 원칙으로
  불필요, `docs/CONTRACT.md` 설계 원칙) · BidWatch 워커에서 안정 동작 확인(→ BidWatch 자동 수집 Phase 몫).
  (부분 실패 보존·에러 정보는 Phase 004로, 429 백오프는 v1.3으로 옮겼다.)

## 결정된 것 / 미정인 것

**확정**
- 수집기는 가져오기만 — 저장·매칭·스케줄·캐시는 BidWatch (2026-04-06, `docs/CONTRACT.md` 설계 원칙)
- 키워드 없이 기간 전체 수집, 필터는 BidWatch 조회 시점 (lets_portal의 키워드별 호출을 버림 — `Phase_001.md`)
- GenericScraper는 사이트별 수집기가 아니라 config 엔진 — BidWatch의 AI가 config를 만든다 (`Phase_003_decisions.md`)
- 나라장터 fetch_detail은 None, 상세는 수집 시 extra (2026-04-13, `Phase_003_detail.md`)
- 2026-09-23 작업 체계를 greenfield 템플릿으로 이식: Phase 로그는 실패한 접근이 있을 때만 · 테스트/서류 전담 agent 대신
  메인 agent가 사다리대로 검증하고 Phase 통합 테스트만 `qa-tester`(foreground) · 화면 규칙은 해당 없음(라이브러리)

- errors 누적 방식 = `_fetch` 세 번째 반환 요소(인스턴스 누적은 동시 호출 시 섞여 버림) · 나라장터 확장 3메서드는 반환형 유지,
  실패는 예외 (2026-09-23 사용자 확정, `docs/CONTRACT.md`)
- 숨기지도 더하지도 않는다 — 원문 전부 전달, 표준 필드는 파생 없이 타입 통일만 (2026-09-25 사용자 확정, `docs/CONTRACT.md` 설계 원칙 첫 항목)

**미정**
- 공기업 API 5종의 필요성 — BidWatch 수요 확인 후
- 원칙 ② `status` 처리 방식(제거=major / 출처 명시값만 / BidWatch가 end_date로 계산) — REQUIREMENTS 미결 질문

## 사용자 실테스트 대기

- **v1.5.0 기관 수집기 4종 fetch_detail(bidwatch 세션 몫)** — `docs/handover/v1.5.0.md`. `pip install -e`·팝업 표시(금액 이름별·일정·담당자·첨부)·
  d2b 한도 공유·가스 표준 계약조건 첨부 구분·interface.md 교체. 반영 확인은 그 문서 §5.

- **v1.4.0 자체조달 기관 수집기 4종(bidwatch 세션 몫)** — `docs/handover/v1.4.0.md`. 출처 등록(스키마 게이트)·bidwatch 키 활용신청·금액 이름별 표시(`-`)·
  interface.md 교체·`pip install -e`. 반영 확인은 그 문서 §5.

> 2026-09-25부터 bidwatch에 넘길 상세는 `docs/handover/v<버전>.md`에 쓰고 여기는 한 줄 링크만 둔다(아래 두 항목은 그 전 형식).
> 이 저장소 세션은 bidwatch 폴더를 수정하지 않는다 — 반영은 bidwatch 세션 몫.

- **v1.3.0 알리오 fetch_detail(bidwatch 세션 몫)** — `docs/handover/v1.3.0.md`. `SKIP_DETAIL_TYPES`·병합 조건(0 버림·빈 첨부 "조회함" 표시)·
  팝업 첨부·원문 링크(onbid 포함)·interface.md 교체. §5 반영 기록됨(2026-09-25) — 남은 것: 팝업 화면 사용자 실테스트(bidwatch plan.md).
- **v1.2.5 extra 원문 전부·확장 3메서드 빈 ID·선택 필드 null(bidwatch 세션 몫)** — `docs/handover/v1.2.5.md`. NoticeModal 키 26개 대응·interface.md 교체·
  오픈 전 재수집·httpx 로거 레벨. 반영 확인은 그 문서 §5(2026-09-25 인계).
- **BidWatch 연동(bidwatch 세션 몫)** — ① 정기·시험 수집의 `GenericScraper(...)` 3곳(`tasks/collect_scraper.py`·`services/scraper_ai.py`·
  `routers/sources.py`)에 `event_hooks={"request": [guard_request]}` 꽂기 ② v1.1부터 `is_partial`/errors가 채워진다 —
  보조금24 정기 수집은 이제 절단 보고 대신 정상 건수(1일 24건 수준)로 온다, GenericScraper는 max_pages 절단이 errors로 온다(기본 3페이지)
  ③ bidwatch venv에서 `pip install -e` 재실행(메타데이터가 1.0.0으로 남아 있음, 코드는 editable이라 이미 1.1.0)
- **v1.2.4 항목 건너뜀·셀렉터 불일치 보고(bidwatch 세션 몫)** — `is_partial=True`가 늘어날 수 있다: 필수 필드(ID·제목) 빠진 항목은
  "항목 파싱 예외로 N건 건너뜀 — 사유 n건", GenericScraper 행은 잡혔는데 0건이면 "페이지 N: 셀렉터 불일치 의심 — 목록 행 R개 …".
  정기 수집에서 이 두 메시지를 수집 이력·알림에 어떻게 띄울지 확인(특히 셀렉터 불일치 = 손 설정 사이트 개편 신호).
  `docs/interface.md` §errors 표 두 행은 양쪽 저장소에 반영(2026-09-25, cmp 동일)

## 보류 항목 (나중에 할 것)

> 조사만 하고 미룬 것을 여기 남긴다. **다시 조사하지 않아도 되도록 실측 결과와 근거까지** 적는다.

- **debt-audit 2026-09-24 (5afbc99 반영본)** — 1군(GenericScraper 정렬 가정·헛통과 테스트·공통 계약 테스트)은 Phase 005에 흡수(위 4~6). 나머지는 **결정 대기**:
  - 실측 필요: **기업마당 정렬 가정**(`bizinfo.py:81-84` "마지막 3건 전부 오래됨"에서 멈춤 — 5afbc99와 같은 유형, 실제 목록 순서 역전 수 미측정) ·
    K-Startup `fetch_detail`의 `cond[pbanc_sn::EQ]`가 실제로 먹는지(응답 ID ≠ 요청 ID 검사 없음 — 무시되면 다른 공고 본문을 조용히 반환)
  - 저위험 결함: ~~K-Startup odcloud `code<0` 미검사·`totalCount` 사용(보조금24는 `matchCount`)~~ → Phase 008 4로 흡수 / 보조금24 `신청기한` 기간 형식이면 시작일이 end_date로 /
    기업마당 날짜 형식 변경 시 cutoff 필터가 조용히 꺼짐 / 기업마당·K-Startup·보조금24 total이 문자열·null이면 TypeError로 전체 손실 /
    GenericScraper health_check·페이지 오류 메시지 키 마스킹 누락 / 알리오 `old_pages` 전부 건너뛴 페이지에서 리셋(주석은 "세지 않는다") /
    (qa-tester 2026-09-25) httpx `_client` INFO 로그가 요청 URL의 `serviceKey`·`crtfcKey`를 평문으로 싣는다 — 이 패키지 로거가 아니라 소비자가
    루트 로거를 INFO로 열 때 노출. `create_client`에 마스킹 필터를 붙일지 결정 대기(`scripts/_tmp/qa_phase006_integration.log`에 실제로 평문 키가 남았다 — gitignore 영역)
  - (Phase 009, 2026-09-26) **기관 수집기 평일 등록 지연 미측정** — 조사·실측일이 추석 연휴(9/24~26)라 "어제~오늘 창 0건"이 지연인지 공고 없음인지
    못 가렸다. 9/29 이후 평일에 LH·가스·d2b 경쟁을 어제~오늘 창으로 몇 시간 간격 재측정 → handover v1.4.0 §2-3 권장 days 조정.
    수자원 빈 본문 경계도 미확인(53KB 성공·발주계획 411건 실패) — 한 달치가 커지면 50건 페이지로 내려가고 겹침을 errors로 알린다.
  - (qa-tester 2026-09-25, Phase 008) **계약 공사 days=3 건수 차이 미확인** — 원문 날짜별 합 10,593 vs `collect_contracts` 10,572(21건 차).
    같은 bid_no 중복 제거인지(빈 `dcsnCntrctNo` → `untyCntrctNo` 폴백 충돌 포함) 항목 건너뜀인지 미판별(경고 로그 캡처 전 콘솔 인코딩 오류). days=1은 62=62 일치.
    확인하려면 공사 3일분 ~106호출 — 한도 여유 있는 날 경고 로그와 bid_no 중복 수를 함께 센다 /
    **통합 테스트 공백**: 확장 3메서드(`collect_contracts` 등) 실호출 테스트 없음 · `test_integration_phase1.py`의 `test_collect`는 0건이어도 통과 —
    이번 공사 누락도 고정 응답 픽스처가 가렸다(실제 태그로 교체함)
  - **안 함(2026-09-25 사용자)**: GenericScraper status를 게시일로 판정(어제 공고가 closed) — 바꾸면 소비자 값이 바뀌는 일반 트랙인데,
    BidWatch가 GenericScraper 공고의 상태 배지를 숨겨 쓰지 않는다. BidWatch가 이 status를 쓰기 시작하면 다시 연다.
  - 일반 트랙: interface.md GenericScraper `collect(days=30)`인데 실제 기본 1 ·
    429 재시도 nara에만 · `_fetch_extended` 부분 결과 보존 없음
  - 정리 Phase 후보: 중복 로직(페이지 루프·절단 문구 4종·cutoff 7곳·health_check 7벌·extra 비우기 9벌·금액 파싱 2갈래) /
    문서 복수 원본(버전 표기 CLAUDE.md·plan.md v1.1.0·interface.md v1.2.0 · plan.md·README에 알리오와 v1.2.x 기록 없음 · "v1.2=JSON 모드" 이름 충돌 ·
    훅 개수 · qa-tester.md 자리표시자 · status `cancelled` · F-008 상태 · CONTRACT.md 알리오 반영 칸 · pre_ready.md 구 번호)

- **템플릿 규칙 위반 — 이식 시점(2026-09-23) 발견**. 조용한 실패·절단·session_init 무검사·행 파싱 예외 debug는 Phase 004에서 해소. 남은 것:
  - `nara.py` `_split_date_range`가 경계일을 두 범위에 겹쳐 넣는다 — `collect()`의 중복 제거가 흡수(의도적 유보, `Phase_002_review.md` §3).
- **수용 기준의 테스트 미대응** — F-008(fetch_detail) 단위 테스트가 없다(`docs/REQUIREMENTS.md` 항목별 표시). 다음에 건드릴 때 테스트부터.
  (F-002는 Phase 004에서 대응.)
- **같은 규칙의 복제 — XML 파싱**: `nara._parse_xml_items` ↔ `smes._parse_xml_response` 대조 테스트 없음(CLAUDE.md '구조').
- **통합 테스트가 실제 API를 부른다** — `tests/test_integration_phase1.py`(`-m integration`)는 `load_dotenv()`가 상위 디렉토리로
  올라가며 `.env`를 찾아 워크트리에서도 메인 체크아웃의 키로 실호출한다(data.go.kr 일 한도 소모). 루틴 실행은 `-m "not integration"`.
- **이식 시 미추적 파일 처리(2026-09-23 사용자 결정)** — `docs/work_log/phase2_new.md` → `work_log/Phase_003_decisions.md`(내용 불변,
  `Phase_003_detail.md`가 "선행 작업: phase2_new.md"로 가리키는 파일) / `docs/bid-collectors-detail-api-spec.md`(BidWatch의 fetch_detail
  개발 요청서, 구현 완료) → `docs/archive/` / `docs/project_management_guide.md`는 템플릿 체계가 대체 — 커밋하지 않음, 메인 체크아웃에서 삭제 여부는 사용자 결정.
