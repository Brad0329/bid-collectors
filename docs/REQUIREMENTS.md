# bid-collectors 요구사항 (단일 원본)

> 기능 요구사항 + 수용 기준의 단일 원본. **요구사항이 바뀌면 대화로 합의만 하지 말고 이 파일을 즉시 갱신.**
> 완료 판정은 항상 이 문서를 기준으로 한다(에이전트를 쓰든 직접 대조하든).
> **새 F-번호는 CLAUDE.md의 '요구사항 ID 대장'에서 받아간다. 번호 재사용 금지.**
>
> **F-001~F-009는 2026-09-23 v1.0.0 구현에서 역작성했다** — 코드의 현재 동작을 기준으로 적었고,
> 각 기준 뒤에 대응 테스트를 `→ 테스트명`(파일은 `tests/test_<수집기>.py`)으로, 없으면 `→ 테스트 없음`으로 표시했다.
> 테스트 없는 기준은 그 기능을 다음에 건드릴 때 테스트부터 붙인다.
> (고정 날짜 픽스처로 27건이 실패하던 `(현재 실패)` 표시는 Phase 004에서 상대 날짜로 고쳐 걷어냈다.)
> 공개 계약(필드·시그니처)의 원본은 `docs/interface.md`, 계약 변경 결정은 `docs/CONTRACT.md`.

## 시스템 목표
- 공공기관 입찰공고·지원사업 공고를 여러 외부 출처(data.go.kr 등 공공 API, 임의 HTML 게시판)에서 가져와
  **하나의 표준 모델(`Notice`)과 수집 메타데이터(`CollectResult`)로 돌려주는 파이썬 라이브러리.**
  소비자는 BidWatch(구독형 입찰공고 SaaS)다. 외부 사이트에서 공고를 가져오는 일은 전부 이 패키지 몫이고,
  DB 저장·키워드 매칭·스케줄링·AI 설정(scraper_config) 생성·캐싱은 소비 서비스 몫이다.

## 사용자/권한
- 사용자 = 이 패키지를 import하는 소비 코드(BidWatch). 사람 사용자·권한 개념은 없다.
- 인증은 외부 API 키뿐 — 생성자 인자 또는 환경변수(`DATA_GO_KR_KEY`, 기업마당만 `BIZINFO_API_KEY`).

## 공통 계약 (모든 수집기)
- 모든 수집기는 `BaseCollector`를 상속하고 `_fetch()`만 구현한다. 소비자는 `collect(days, **kwargs)`를 부른다.
- **수용 기준**:
  - [x] 생성자 `api_key`가 있으면 그것을, 없으면 환경변수를 쓴다 → `test_base::test_api_key_from_constructor`·`test_api_key_from_env`
  - [x] 키가 둘 다 없으면 `ValueError` → `test_base::test_missing_api_key_raises`
  - [x] `collect()`는 `CollectResult`를 반환하고 `(source, bid_no)` 기준으로 중복을 제거한다(`total_fetched` ≥ `total_after_dedup`) → `test_base::test_collect_deduplicates`
  - [x] `_fetch()`가 예외를 던지면 `errors`에 메시지, `is_partial=True`, 공고 0건 → `test_base::test_collect_handles_fetch_error`
  - [x] `health_check()`는 예외를 던지지 않고 `{"status": "ok"|"error", ...}`를 반환한다 → 수집기별 `test_health_check_*`
  - [x] 지원하지 않는 수집기의 `fetch_detail()`은 `None` → 테스트 없음(기본 구현이 한 줄)
  - [x] (v1.1) `_fetch`가 세 번째 요소로 errors를 주면 공고는 보존하고 `is_partial=True` → `test_base::test_fetch_errors_make_partial_and_keep_notices`·`test_fetch_empty_errors_not_partial`
  - [x] (v1.1) 페이지 요청 실패는 앞 페이지 결과를 남기고 errors에 원인을 담는다 — 1페이지 실패면 0건 + errors(“장애”와 “공고 없음” 구분) → 수집기별 `test_*_error_graceful`·`test_http_error_returns_empty`·`test_second_page_*`
  - [x] (v1.1) `max_pages` 상한에서 멈추면 잘렸다는 사실과 전체 건수를 errors로 알린다(기업마당·K-Startup·보조금24·중소벤처기업부) → 수집기별 `test_max_pages_truncation_reported`
  - [x] (v1.1) errors·health_check 메시지에 API 키(쿼리 파라미터 값·원문·URL 인코딩)가 실리지 않는다 → `TestMaskSecret` 2건·`test_exception_message_key_masked`·수집기별 `test_api_key_masked_in_errors`·`test_request_failure_masks_key`
  - [x] (v1.2.4, Phase 005) 2건 중 1건의 필드가 null·형식 이상이면 나머지 1건은 반환되고, 건너뛴 건수와 사유가 errors에 담긴다
    (기업마당·K-Startup·보조금24·중소벤처기업부·나라장터 입찰공고·알리오 — 종전 기업마당·보조금24는 0건, 나머지는 errors 없이 1건)
    → `test_item_contract::test_null_title_skips_only_that_item`·`test_bad_format_skips_only_that_item`(수집기별 parametrize)
  - [x] (v1.2.4, Phase 005) ID 필드가 없거나 빈 항목은 `{접두사}-`로 합쳐지지 않고 건너뛰어 errors에 보고된다(숫자 0은 유효 ID)
    — 3건 입력 시 0건 + errors에 "필수 필드 없음" 사유 한 줄과 건수 3 → `test_item_contract::test_missing_id_items_are_not_merged`·`test_zero_id_is_valid`
    (새 수집기 편입 강제: `test_every_collector_has_a_case`)
  - [x] (v1.2.4, Phase 005, F-007) GenericScraper: 목록 행이 잡혔는데 제목·날짜 추출 결과 공고 0건·cutoff 이전 행 0건이면 errors에 셀렉터 불일치 의심과 행 수
    (목록 행 0개는 빈 게시판과 구분 불가라 보고하지 않음) → `test_generic_scraper::TestSelectorMismatch` 4건
  - [x] (v1.2.5, Phase 006 B) 항목 하나의 값 이상(null·비문자열)이 항목 단위 계산 어디서 나도 결과 전체를 지우지 않는다 — 기업마당 `_is_within_cutoff`·
    보조금24 `_is_business_target`는 어떤 값에도 예외를 던지지 않고, 선택 필드가 null인 항목은 건너뛰지 않고 그 필드만 비운 채 반환된다
    (2건 중 1건의 선택 필드 null → 2건 반환, errors 없음) → `test_item_contract::test_null_optional_field_keeps_item`(6종) · `test_bizinfo::test_is_within_cutoff_never_raises`(6)·
    `test_null_reqst_period_keeps_item` · `test_subsidy24::test_is_business_target_never_raises`(3)·`test_null_organization_keeps_item`.
    형식 이상(제목이 list) 케이스는 `test_bad_format_skips_only_that_item`이 기업마당·K-Startup·보조금24·나라장터·알리오 5종에 돈다(중소벤처는 XML이라 값이 전부 문자열 — 만들 수 없음)
- **원칙 — 숨기지도 더하지도 않는다 (2026-09-25 사용자 확정, 결정 기록 `docs/CONTRACT.md` 설계 원칙 첫 항목)**: 출처가 주는 것은 전부
  가져와 정확히 전달되는 형식으로 넘기고, 쓸지 말지는 BidWatch가 정한다. 아래 두 기준은 **모든 수집기(나라장터 확장 3메서드 포함)의 완료 기준**이다 —
  새 수집기도 이 둘을 채워야 완료. 상태: ① **완료**(v1.2.5, Phase 006, 2026-09-25 — GenericScraper·`fetch_detail`은 범위 밖, plan.md 보류) / ② **미착수**(일반 트랙, Phase 미발급).
  - [x] (원칙 ①, v1.2.5 Phase 006) `extra`에 응답 항목의 비어 있지 않은 필드 전부가 원래 이름 그대로 담긴다 — 고정 응답의 비어 있지 않은 필드 수와 `extra` 키 수가
    같고, 이름을 바꾼 키가 없다(확정 2026-09-25 기본안: 0·False 포함, None·빈 문자열·공백만 제외, XML 반복 태그는 list·자식 있는 태그는 dict, 요청 문맥
    `bid_type`·`data_type`은 뺀다 — bid_no 접두사에 있다, HTML 값도 원문 그대로) → `test_item_contract::test_extra_has_every_nonempty_field`·
    `test_extra_keys_are_original_names`(6종 parametrize) · `test_raw_fields.py` 6건 · 통합 `TestNaraIntegration::test_extra_matches_raw_response`(3서비스 1페이지
    300건, 2026-09-25)·`test_alio::test_real_api_extra_matches_raw_items`(10건). 해소된 종전 위반(2026-09-25 실측, `scripts/_tmp/nara_field_coverage.py`):
    나라장터 응답 태그 중 용역 113개의 41개·물품 101의 40·공사 143의 37만 읽음 / 영어로 이름을 바꾼 키 / 이름이 틀려 항상 빈값인 키 2개(`cntrctMthdNm`·`bidQlftcRgstDt`) /
    공사 배정예산 `bdgtAmt` 미수신(extra에는 담긴다 — `budget` 표준 필드 반영은 원칙 ②). 공고첨부 태그 20개(`bidNtceFlNm*`·`bidNtceFlUrl*`) 루프는
    명세에도 없는 태그라 v1.3.1(Phase 008)에서 삭제
  - [ ] (원칙 ②) 표준 필드는 출처가 준 값을 통일된 타입으로만 담는다 — 추정·대체·합성·절단·상수·기본 필터가 없다 → 테스트 없음.
    현재 위반(2026-09-25): `status`를 마감일로 추정(전 수집기 — 나라장터 취소공고 `ntceKindNm`가 ongoing으로 나감) · `budget` 배정예산→추정가격 대체(F-001) ·
    `category` 대>중 합성(F-001) · `region`에 수요기관명 `dminsttNm`(F-001) · content 500자 절단(F-003·F-006) · organization 상수(F-006 "중소벤처기업부",
    F-003 폴백 "창업진흥원") · `only_ongoing=True` 기본값(F-003). `only_business`(F-005)는 기본 False라 위반 아님 — 유지.
  - 적용 트랙: ①은 계약 밖(`extra`) → 저위험 / ②는 BidWatch가 받는 값이 바뀜 → 일반 트랙(CONTRACT.md 변경안 → 사용자 확인 → 반영).
    ②를 반영하면 F-001 budget·F-003 content 절단·only_ongoing·F-006 content 절단 기준은 그때 고쳐 쓴다(지금은 현재 동작 그대로 둔다).

## 기능 요구사항

### F-001: 나라장터 입찰공고 수집 (`NaraCollector.collect`)
- **설명**: 조달청 입찰공고 API(용역·물품·공사 3개 서비스)를 날짜 범위로 조회. 7일 단위로 범위를 나누고
  100건/페이지로 totalCount까지 넘긴다. 429는 30초 대기 후 3회 재시도. `bid_types` kwargs로 서비스 선택.
- **수용 기준**:
  - [x] days를 7일 단위 범위(`yyyyMMdd0000`~`yyyyMMdd2359`)로 나눈다 → `TestSplitDateRange` 6건
  - [x] `resultCode != 00` 응답은 `ValueError` → `test_error_response_raises_valueerror`
  - [x] bid_no = `{용역|물품|공사}-{bidNtceNo}-{bidNtceOrd}`(차수 없으면 생략) → `test_bid_no_format`·`test_bid_no_without_ord`
  - [x] budget = 배정예산, 없으면 추정가격(0원도 유효값) → `test_budget_and_est_price`
  - [x] 공고규격서 1~10(`ntceSpecDocUrl`·`ntceSpecFileNm`, 이름 없으면 `규격서{i}`)이 attachments → `test_attachments_parsing`
    (v1.3.1: 종전 "공고첨부 1~10"의 `bidNtceFl*`는 명세·실측 3,360건에 없는 태그라 삭제 — 다른 첨부는 별도 오퍼레이션에만 있다)
  - [x] 여러 페이지를 totalCount까지 넘긴다 → `test_multi_page_pagination`
  - [x] 429를 받으면 재시도해 성공 응답을 쓴다 → `test_429_retry_logic` · 재시도 소진 시 0건 + errors → `test_429_exhausts_retries`
  - [x] 한 서비스의 `resultCode != 00`(쿼터 초과 포함)이 앞서 수집한 서비스 결과를 버리지 않는다 — errors에 기록하고
    그 서비스의 남은 기간은 요청하지 않는다 → `test_quota_error_keeps_earlier_services`
- **상태**: 완료 (2026-04-06, 상세 필드 보강 2026-04-13, 부분 결과 보존 2026-09-23 Phase 004)

### F-002: 나라장터 확장 — 낙찰·계약·사전규격 (`collect_awards` / `collect_contracts` / `collect_pre_specs`)
- **설명**: 입찰공고와 같은 루프(`_fetch_extended`)로 세 API를 조회해 **`list[Notice]`를 바로 반환**한다
  (`CollectResult` 아님 — `collect()` 계약 밖의 확장 메서드). bid_no 접두사 `낙찰-`·`계약-`·`사전규격-`.
  사전규격 API만 `ServiceKey`(대문자 S). BidWatch는 사전규격을 `nara_prespec` 출처로 연쇄 수집한다.
- **수용 기준**:
  - [x] 사전규격: bid_no = `사전규격-{type}-{bfSpecRgstNo}`, 의견마감일이 end_date, 규격서 1~5가 attachments, `ServiceKey` 파라미터 → `TestNaraExtended::test_pre_specs_mapping`
  - [x] 낙찰: status가 항상 `closed`, extra에 낙찰자·낙찰률·참가자 수 → `test_awards_mapping`
  - [x] 계약: bid_no = `계약-{type}-{dcsnCntrctNo}` → `test_contracts_mapping`
  - [x] (v1.3.1, Phase 008) 계약 제목은 업무별 태그 — 용역·물품 `cntrctNm`, 공사 `cnstwkNm`. 공사 N건(제목 `cnstwkNm`만) → N건 반환·제목 일치·건너뜀 경고 0
    → `test_contracts_construction_all_items_kept`. 종전엔 `cntrctNm`만 봐서 공사가 전건 건너뛰어졌다(2026-09-25 실측 공사 1일 61/61건 → 수정 후 61건 반환)
  - [x] API 에러 응답 시 예외가 호출자에게 그대로 전파된다(부분 결과 없음 — 반환형이 list라 errors를 담을 곳이 없다, CONTRACT.md) → `test_api_error_propagates`
  - [x] (v1.1) 요청 재시도 소진도 조용한 빈 결과가 아니라 `RuntimeError`, 메시지에 키 없음 → `test_request_failure_raises_masked`
  - [x] (v1.2.5, Phase 006 A) ID(낙찰 `bidNtceNo`·계약 `dcsnCntrctNo`|`untyCntrctNo`·사전규격 `bfSpecRgstNo`|`refNo`)나 제목(낙찰 `bidNtceNm`·계약 `cntrctNm`)이 없는 항목은
    `낙찰-용역-`처럼 접두사만으로 합쳐지지 않고 건너뛴다(계약 제목은 v1.3.1부터 `cntrctNm`|`cnstwkNm`) — 3건 입력 시 0건, 경고 로그에 사유별 건수(반환형이 list라 errors 채널 없음 — CONTRACT.md)
    → `TestNaraExtended::test_awards_without_id_are_skipped_not_merged`·`test_contracts_without_id_are_skipped_not_merged`·`test_pre_specs_without_id_are_skipped_not_merged`(caplog)
  - [x] (v1.2.5, 원칙 ①) 확장 3메서드의 `extra`도 응답 태그 전부·원래 이름(`bid_type`·`data_type` 없음) → `test_awards_mapping`
- **상태**: 완료 (2026-04-11, 실 API 1일분 검증: 낙찰 15·계약 6,718·사전규격 322건 — `work_log/Phase_003.md` §11. 테스트 대응 2026-09-23, 빈 ID 병합 방지·원문 extra 2026-09-25 v1.2.5,
  계약 공사 제목 태그 2026-09-25 v1.3.1)

### F-003: K-Startup 사업공고 수집 (`KstartupCollector`)
- **설명**: odcloud 형식 JSON. 접수시작일이 cutoff(자정 절삭) 이전이면 제외. `only_ongoing=True`(기본)면
  모집중만 서버 필터. 상태는 API의 `rcrt_prgs_yn`으로 판정. content 500자 절단(전문은 `fetch_detail`).
- **수용 기준**:
  - [x] bid_no = `KSTARTUP-{pbanc_sn}` → `test_bid_no_format`
  - [x] cutoff 이전 접수시작 공고는 None → `test_cutoff_filtering_old_item_returns_none`
  - [x] url = 상세 → 신청 → 안내 URL 순 폴백 → `test_url_fallback_chain_*`
  - [x] `only_ongoing=True`면 `cond[rcrt_prgs_yn::EQ]=Y`를 보낸다 → `test_only_ongoing_param`
  - [x] 여러 페이지 수집 → `test_multi_page_pagination`
  - [x] HTTP·네트워크 오류 시 예외 없이 반환하고 errors에 원인 → `test_http_error_graceful`·`test_network_error_graceful`
  - [x] (v1.3.1, Phase 008) 페이지 종료·절단 문구의 전체 건수는 필터 적용 후 `matchCount`(`totalCount`는 필터 무관 전체) — matchCount 150·totalCount 30000에서
    요청 2회, max_pages=1이면 "전체 150건 중" → `test_pagination_stops_at_match_count_not_total`·`test_truncation_message_reports_match_count`
    (2026-09-25 실측: 진행중 matchCount 230·totalCount 30,168, 요청 4→3회, 수집 228건 동일)
  - [x] (v1.3.1) odcloud `code < 0` 응답은 errors에 코드·메시지(보조금24와 같은 검사) → `test_odcloud_error_code_reported`
    (실측상 잘못된 키는 게이트웨이 403으로 와서 HTTP 오류 경로를 탄다 — 이 검사는 방어용)
- **상태**: 완료 (2026-04-06, matchCount·code 검사 2026-09-25 v1.3.1)

### F-004: 기업마당 지원사업 수집 (`BizinfoCollector`)
- **설명**: 별도 키 `BIZINFO_API_KEY`. API에 날짜 필터가 없어 전체를 받으며 `creatPnttm` 기준 클라이언트 필터.
  정렬이 보장되지 않아 페이지 마지막 3건이 모두 cutoff 이전일 때만 조기 종료.
- **수용 기준**:
  - [x] bid_no = `BIZINFO-{pblancId}` → `test_bid_no_format`
  - [x] `reqstBeginEndDe`("A ~ B")에서 시작·종료일을 뽑는다 → `test_date_parsing_period_format`
  - [x] cutoff 이전 생성 공고는 None, 생성일 없으면 통과 → `test_old_item_returns_none`·`test_empty_creatpnttm_not_filtered`
  - [x] 인쇄본·첨부 두 파일을 attachments로 → `TestParseAttachments` 5건
  - [x] 여러 페이지 수집 → `test_multi_page_pagination`
  - [x] 2페이지 실패 시 1페이지 결과 보존 + errors → `test_second_page_failure_keeps_first_page`
- **상태**: 완료 (2026-04-06)

### F-005: 보조금24 공공서비스 수집 (`Subsidy24Collector`)
- **설명**: odcloud gov24 v3. `cond[수정일시::GTE]`로 서버 필터. 한국어 필드명. 서비스 15113968 별도 활용 신청 필요.
  `only_business=True`면 키워드(`BUSINESS_KEYWORDS`)로 기업 대상만 남긴다(휴리스틱).
- **수용 기준**:
  - [x] bid_no = `GOV24-{서비스ID}`, 서비스ID·서비스명 중 하나라도 없으면 None → `test_bid_no_format`·`test_missing_*_returns_none`
  - [x] url = 상세조회URL, 없으면 gov.kr 기본 URL → `test_url_fallback_to_gov_kr`
  - [x] 신청기한이 end_date, 마감 지나면 closed → `test_end_date_from_deadline` · 통합 매핑 `test_full_item_mapping`
  - [x] `only_business=True`면 기업 키워드 없는 항목 제외 → `test_only_business_filter`
  - [x] API 에러 코드(`code < 0`) 시 예외 없이 반환하고 errors에 코드·메시지 → `test_api_error_response`
  - [x] cutoff를 자정으로 절삭하고, API 값과 같은 `YYYYMMDDHHMMSS` 형식으로 보낸다 → `test_cutoff_truncated_to_midnight`
    (v1.0.x는 `YYYY-MM-DD HH:MM:SS`로 보내 문자열 비교가 거의 전부를 통과시켰다 — 1일치 10,498/10,933건, 50페이지 상한에서 조용히 잘림.
    2026-09-23 실측: 고친 형식으로 1일 24·7일 213·30일 1,406건)
- **상태**: 완료 (2026-04-06, cutoff 자정 절삭·서버 필터 형식 수정 2026-09-23 Phase 004)

### F-006: 중소벤처기업부 사업공고 수집 (`SmesCollector`)
- **설명**: XML, **HTTP**(HTTPS 아님) 엔드포인트. `startDate`/`endDate` 서버 필터. 예산 없음(응답에 지원 규모 태그가 없다).
- **수용 기준**:
  - [x] bid_no = `MSS-{itemId}` → `test_bid_no_format`
  - [x] budget은 항상 None → `test_budget_always_none` (v1.3.1: 종전 `suptScale` 손 매핑은 명세 swagger 11키·실측 88건에 없는 태그라 삭제 — 값 불변)
  - [x] fileName/fileUrl 쌍 추출(이름 부족 시 `첨부파일N`) → `TestExtractAttachments` 3건
  - [x] content 500자 절단 → `test_content_truncation_500_chars`
  - [x] HTTP 오류·XML 에러 응답 시 0건 + errors에 원인 → `test_http_error_returns_empty`·`test_xml_error_response_returns_empty`
  - [x] 2페이지 resultCode 에러 시 1페이지 보존 → `test_second_page_xml_error_keeps_first_page`
- **상태**: 완료 (2026-04-06, 없는 예산 원천 제거 2026-09-25 v1.3.1)

### F-007: GenericScraper — config 기반 HTML 게시판 수집 (`GenericScraper` / `ScraperConfig`)
- **설명**: `ScraperConfig`(Pydantic)로 검증한 설정만으로 임의 게시판을 파싱. GET page/offset·POST form/JSON·
  페이지네이션 없음 4패턴, JS onclick 링크 정규식, 세션 쿠키 사전 요청, 비 UTF-8 인코딩, `verify_ssl`.
  bid_no = `SCR-{source_key}-{md5(title+link) 10자리}`. API 키 불필요. config는 소비자(BidWatch의 AI)가 만든다.
- **수용 기준**:
  - [x] 필수 6필드 누락·source_key 비ASCII/대문자/특수문자·max_pages 범위(1~50) 위반은 ValidationError → `TestScraperConfig` 19건
  - [x] link_js_regex/link_template은 짝으로만, page_param_key는 post_data와만, offset_size는 `{offset}`과만 → 같은 클래스
  - [x] cutoff 이전 행은 건너뛰고, 한 페이지가 전부 cutoff 이전이면 다음 페이지를 요청하지 않는다 → `test_cutoff_filtering`·`test_old_content_early_stop`
  - [x] 상대 링크를 link_base(없으면 list_url) 기준 절대 URL로 → `test_relative_url_*`
  - [x] 같은 제목·링크는 같은 bid_no → `test_deterministic`
  - [x] 2페이지 요청 실패 시 1페이지 결과는 남기고 errors에 원인 → `test_http_error_partial_collect`
  - [x] `verify_ssl=False`가 실제 transport에 적용된다 → `test_http::test_verify_false_disables_certificate_check` (2026-09-23 `152f930`)
  - [x] 1페이지 요청이 실패하면 0건과 함께 errors에 원인이 담긴다 → `test_first_page_failure_reports_error`
  - [x] max_pages에 도달해 멈추면(마지막 페이지에 cutoff 이전 행 없음) 잘렸다는 사실을 알린다, cutoff에 닿았으면 알리지 않는다
    → `test_max_pages_truncation_reported`·`test_no_truncation_when_cutoff_reached`
  - [x] 소비자가 요청 검사 훅(SSRF 방어)을 `event_hooks`로 주입할 수 있다 — session_init_url·페이지·리다이렉트·health_check 전부에 걸리고,
    훅이 던진 예외는 errors에 기록되며 차단된 URL로는 요청이 나가지 않는다 → `TestEventHooks` 3건·`test_http::TestCreateClientEventHooks`
  - [x] 페이지네이션이 없으면 1페이지만 요청한다(같은 URL을 max_pages번 받지 않는다) → `test_no_pagination_fetches_single_page`
  - [x] 세션 초기화 요청 실패·행 파싱 예외로 건너뛴 행 수를 errors로 알린다 → `test_session_init_failure_reported`·`test_row_parse_exception_counted`
- **엣지케이스**: grid_selector가 없으면 0건 / 제목 없는 행은 건너뜀(정상) / `skip_no_date=False`면 날짜 없이 수집.
- **상태**: 완료 (2026-04-11, 실패·절단 보고·요청 훅 2026-09-23 Phase 004)

### F-008: 상세 조회 (`fetch_detail(bid_no)`)
- **설명**: 목록 수집에 없는 정보를 공고 1건 단위로 보충한다. 결과 캐싱은 소비자 몫.
  K-Startup(`cond[pbanc_sn::EQ]` 단건 조회 → content 전문 + 대상·신청방법 등 dict, 실패는 None)·알리오(v1.3.0 — 수용 기준은 F-010, 실패는 예외) 구현.
  나라장터는 API가 단건 조회·사업개요를 지원하지 않아 None — 대신 수집 시점에 extra를 채운다(`work_log/Phase_003_detail.md`).
- **수용 기준**:
  - [ ] K-Startup: 존재하는 bid_no면 content 전문이 든 dict, 없는 번호·오류면 None → 테스트 없음
  - [ ] 나라장터·기업마당·보조금24·중소벤처기업부·GenericScraper는 None → 테스트 없음 (알리오는 v1.3.0부터 구현 — F-010)
- **상태**: 완료 (2026-04-11, 나라장터 스크래핑 제거 2026-04-13)

### F-009: 공통 유틸 — 날짜 파싱·HTML 정리·상태 판정·HTTP 클라이언트
- **수용 기준**:
  - [x] `parse_date`: `YYYY-MM-DD`·`.`·`/`·`YYYYMMDD(HHMM)`·2자리 연도·`YYYY년 M월 D일`·기간 문자열의 시작일, 실패 시 None → `test_dates.py` 19건
  - [x] `clean_html_to_text`: 태그 제거·블록 태그 줄바꿈·엔티티 복원·연속 줄바꿈 2개로 → `test_text.py`
  - [x] `determine_status`: 마감일이 오늘 이후면 ongoing, 지났으면 closed, 파싱 불가면 ongoing → `test_status.py`
  - [x] `create_client`: User-Agent·타임아웃 기본값, 리다이렉트 추적, transport 인자(verify 등)를 transport로 전달 → `test_http.py`
- **상태**: 완료

### F-010: 알리오 공공기관 입찰공고 수집 (`AlioCollector`)
- **설명**: 알리오 입찰공고 화면이 부르는 공개 JSON(`GET alio.go.kr/occasional/findBidList.json`, 인증 없음, 10건/페이지,
  **등록(seq) 최신순 — 공고일은 뒤섞인다**). 서버 날짜 필터가 없어 기준일 이전 항목은 버리고, 기준일 이후 항목이 없는 페이지가
  2번 연달아 나오면 멈춘다(v1.2.3 — 종전 "첫 오래된 항목에서 멈춤"은 9/22 공고 472건을 errors 없이 놓쳤다). 평일 하루 약
  450~500건이라 기본 max_pages 150(≈3일치). **API 키 불필요.**
  bid_no = `ALIO-{seq}`, organization = 공고 기관(`pname`), 상세 = `bidDtl.do?seq=`. 자체조달 공기업 공고를 받기 위해
  (bidwatch `docs/procurement_sources_research.md` 3-1).
  **fetch_detail(v1.3.0, Phase 007)**: `GET alio.go.kr/occasional/findBidDtl.json?seq=`(인증 없음) → `attachments`(`fileList`) + `content`(HTML 제거) +
  `data.bidDtl`의 비어 있지 않은 필드 전부·원래 이름(원칙 ①, `bFiles` 포함 — 2026-09-25 사용자). 실패는 예외(없는 seq도 HTTP 200 +
  `status:"error"` "시스템 에러입니다"라 장애와 구분 불가 — 2026-09-25 실측·사용자 확정). 수집 경로에서는 부르지 않는다(1건당 호출 +1). 출처: bidwatch 요청서
  `bidwatch/docs/requests/bid-collectors_alio_fetch_detail.md`.
- **수용 기준**:
  - [x] 항목 → Notice(제목 공백 정리·기관·공고일·마감일·상태·상세 URL) → `test_item_maps_to_notice`
  - [x] API 키 없이 생성된다 → `test_no_api_key_needed`
  - [x] (v1.2.3) 기준일 이전 항목이 새 항목 사이에 끼어도 멈추지 않고 뒤의 새 항목까지 받는다(회귀 — 2026-09-24 누락 472건)
    → `test_interleaved_old_item_does_not_stop_collection`
  - [x] (v1.2.3) 기준일 이후 항목이 없는 페이지 2연속에서 멈추고, 1페이지만 오래됐으면 계속 받는다
    → `test_stops_after_two_consecutive_old_pages` · `test_single_old_page_does_not_stop`
  - ~~기준일보다 오래된 항목에서 멈추고 그 뒤와 다음 페이지를 받지 않는다~~ — 폐기(v1.2.3, 등록 순 목록이라 누락을 낳음)
  - [x] 1페이지 실패는 0건 + errors, 2페이지 실패(status≠success 포함)는 1페이지 보존 + errors → `test_first_page_failure_*`·`test_second_page_failure_keeps_first_page`·`test_non_success_status_raises`
  - [x] max_pages 상한 도달 시 절단 사실과 전체 건수를 errors에 → `test_max_pages_truncation_reported_with_total`
  - [x] (v1.3.0) fetch_detail: 첨부 3건 응답 → `attachments` 3건(`name`=fileNm·`url`=fileNo, 순서 유지), 반환 키 집합 ==
    bidDtl의 비어 있지 않은 키 ∪ {`attachments`,`content`}(0 포함·이름 그대로), `content`는 HTML 제거·빈 값이면 `""` → `TestFetchDetail::test_maps_detail`·`test_content_html_stripped`
  - [x] (v1.3.0) fetch_detail: `fileList`가 없거나 null이면 `attachments == []`(None 아님) → `test_no_files_gives_empty_list`
  - [x] (v1.3.0) fetch_detail: `status != success`·HTTP 오류·`bidDtl` 없음·`fileList`가 list 아님 → 예외(메시지에 status·message), `ALIO-{seq}` 형식이 아니면
    요청 없이 `ValueError` → `test_error_status_raises`·`test_http_error_raises`·`test_malformed_raises`·`test_bad_bid_no_raises_without_request`
  - [x] (v1.3.0) `collect()`는 상세 API를 부르지 않는다 → `test_collect_does_not_call_detail_api`
  - [x] (v1.3.0) 실호출: 최근 공고 seq로 상세 조회 → 반환 키 집합 == 비어 있지 않은 키 ∪ 2개, attachments 건수 == fileList 건수 → `test_real_fetch_detail`(integration)
  - [x] seq 없는 항목은 건너뛰고 건수를 errors에 → `test_item_without_seq_is_skipped_and_reported`
  - [x] 필수 필드(seq·rtitle·pname·bdate)가 없거나 비었거나 날짜로 안 읽히면 건너뛰고 **사유(필드 이름)와 건수**를 errors에 —
    공식 API가 없어 형식 변경을 감지하는 유일한 장치(2026-09-24, v1.2.2). seq 0은 유효, 마감일은 선택(실측 474건 중 10건 빈 값)
    → `test_missing_required_field_is_skipped_with_reason`(6) · `test_seq_zero_is_a_valid_id` · `test_missing_deadline_is_kept`
  - [x] 실호출: 최근 3일 1건 이상, 필드 채워짐 → `test_real_api_returns_recent_notices`(integration, 2026-09-24 통과)
- **상태**: 완료 (2026-09-24, v1.2.0 / fetch_detail 2026-09-25, v1.3.0 Phase 007 — 실측 30건 전부 키 집합·첨부 건수 일치)

### 자체조달 기관 수집기 공통 (F-011~F-014, v1.4.0 Phase 009)
- **출처**: bidwatch 요청서 `bidwatch/docs/requests/bid-collectors_institution_collectors.md`(2026-09-25) · 조사 `docs/institution_sources.md` ·
  계약 `docs/CONTRACT.md` 2026-09-26 행(사용자 확인 — bid_no 형식·d2b 수의 2종 포함·budget None + 금액은 extra 원문).
- data.go.kr XML 응답 파싱은 `utils/datagokr.py` 한 곳(4개 수집기 공유) — `resultCode` ≠ 00이면 예외(수집기가 "0건"으로 지정한 코드만 0건),
  **HTTP 200 + 빈 본문은 실패**(수자원 실측: 약 51KB 초과 시), EUC-KR 선언은 바이트 그대로 파싱.
- 공통 수용 기준(4개 모두):
  - [ ] 항목 → Notice: 제목·기관·공고일·마감일·category(출처의 업무 구분)·url, `budget is None`, `extra` = 원문 전부 → 수집기별 `test_item_maps_to_notice`
  - [ ] 항목 계약(null 제목·ID 없음·ID 0·선택 필드 null·extra 원문) → `test_item_contract` CASES에 4종 편입
  - [ ] 1페이지 HTTP 오류 → 0건 + errors(키 마스킹), 2페이지 실패 → 1페이지 보존 + errors → 수집기별 `test_*_page_failure_*`
  - [ ] `resultCode` 에러 코드 → errors에 코드·메시지, 빈 본문(HTTP 200) → errors에 "빈 응답" → `test_datagokr` + 수집기별
  - [ ] totalCount만큼 페이지를 넘기고, max_pages 상한이면 절단 사실과 전체 건수를 errors에 → 수집기별 `test_paginates_*`·`test_max_pages_truncation_reported`
  - [ ] 실호출: 최근 기간 1건 이상, 항목마다 extra 키 수 == 비어 있지 않은 원문 필드 수 → `test_integration_institutions.py`(integration)

### F-011: LH 입찰공고 수집 (`LhCollector`)
- **설명**: `GET apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo`(15159012) — XML **EUC-KR**, 공고일(`tndrbidRegDt`) 범위 `tndrbidRegDtStart/End`,
  numOfRows 상한 없음(1000씩). 빈 결과 = `resultCode 03 NODATA`(0건). bid_no = `LH-{bidNum}` — 정정·취소는 같은 행의 `bidDegree`·공고일이 바뀐다(차수 제외).
  organization "한국토지주택공사", end_date = `tndrdocAcptEndDtm`, category = `cstrtnJobGbNm`(시설공사·용역·지급자재·물품),
  url = LH 전자입찰 상세(알리오 refrUrl에서 확인한 업무별 경로 — 시설공사 Construct·용역 srvcs·지급자재 ctrctgds, 물품은 미확인이라 목록 화면).
- **수용 기준**:
  - [ ] 공통 기준 전부
  - [ ] `resultCode 03` → 0건·errors 없음 → `test_nodata_is_empty_not_error`
  - [ ] EUC-KR 바이트 응답의 한글 제목이 깨지지 않는다 → `test_euc_kr_response`
  - [ ] 업무 구분별 url 경로(3종 + 물품 목록 화면) → `test_detail_url_by_job_type`
- **상태**: 진행

### F-012: 한국가스공사 입찰정보 수집 (`KogasCollector`)
- **설명**: `GET apis.data.go.kr/B551210/bidInfoList2/getBidInfoList2`(15157366) — XML, 공고일(`NOTICE_DT`) 범위 `DOCDATE_START/END`.
  **날짜 누락·이름 오류도 00 + 0건** → 날짜 인자를 항상 보낸다. bid_no = `KOGAS-{NOTICE_CODE}`(10자리). organization "한국가스공사",
  end_date = `END_DT`, category = `WORK_TYPE_NAME`, url = `bid.kogas.or.kr:9443/.../bid_detail_view_notice.jsp?notice_code=&bid_code=001&round=01`(알리오 refrUrl 11건 전부 001/01).
  취소는 `CANCEL_YN` 원문(extra).
- **수용 기준**:
  - [ ] 공통 기준 전부
  - [ ] 요청에 `DOCDATE_START`·`DOCDATE_END`가 늘 들어간다 → `test_request_has_date_range`
- **상태**: 진행

### F-013: 국방전자조달(d2b) 입찰공고 수집 (`D2bCollector`)
- **설명**: `apis.data.go.kr/1690000/BidPblancInfoService`(15158416) 목록 5종 — XML(JSON은 `dcsNo`가 int/str로 섞인다). 오퍼레이션당 100회/일.
  국내경쟁·시설경쟁 = 공고일 범위(`anmtDateBegin/End`) / 국외경쟁 = 공고일 범위 + 개찰일(`opengDateBegin/End` 필수 — 기준일~1년 뒤) /
  국내·시설 공개수의협상 = 공고일 필터가 없어 **견적서 제출마감 오늘~1년 뒤(진행 중 전량)**, start_date None(공고일 필드 없음 — `ntatPlanDate`는 앞으로의 협상 예정일).
  bid_no = `D2B-{구분}-{키}-{차수}`(구분 국내경쟁·국외경쟁·시설경쟁 키 `g2bPblancNo` / 국내수의 키 `{demandYear}{pblancNo}{dcsNo}`·
  시설수의 키 `{pblancNo}{cntrwkNo}`, 차수 5종 모두 `pblancOdr` — `g2bPblancOdr`는 취소·정정에도 그대로라 쓰지 않는다, 2026-09-26 실측). organization = `ornt`(국외경쟁 목록엔 없어 "방위사업청" — 국외 조달은 방위사업청 직접),
  category = `busiDivs`, url = d2b 입찰공고 화면(상세 링크 필드 없음). 목록 하나가 실패해도 나머지 목록 결과는 보존.
- **수용 기준**:
  - [ ] 공통 기준 전부
  - [ ] 5종 각각의 bid_no 형식·제목·마감 필드 → `test_bid_no_by_list`(5)
  - [ ] 취소공고(같은 g2bPblancOdr, pblancOdr만 다름)는 원공고와 다른 bid_no(회귀 — 실측 7일 9건 합쳐짐) → `test_cancel_notice_is_separate_from_original`
  - [ ] 수의 2종의 start_date는 None(협상 예정일을 공고일로 쓰지 않는다) → `test_negotiation_has_no_start_date`(2)
  - [ ] 목록 1종 실패 → 나머지 4종 결과 보존 + errors에 그 목록 이름 → `test_one_list_failure_keeps_others`
  - [ ] 국외경쟁 요청에 개찰일 범위, 수의 2종 요청에 견적서 마감 범위가 들어간다 → `test_list_date_params`
- **상태**: 진행

### F-014: 한국수자원공사 입찰공고 수집 (`KwaterCollector`)
- **설명**: `apis.data.go.kr/B500001/ebid/tndr3/{cntrwkList,servcList,gdsList,dmscptList}`(15101635) — JSON, 날짜 필터는 **월 단위 `searchDt=YYYYMM`**(공고일 기준) 하나뿐 →
  기준일이 든 달부터 이번 달까지 받아 공고일(`tndrPblancDe`, 정수)로 거른다. **큰 응답은 HTTP 200 + 빈 본문**, **페이지 정렬이 불안정**(나눠 받으면
  같은 행이 두 페이지에 나오고 그만큼 다른 공고가 빠진다 — 2026-09-26 용역 142건 중 4건) → 한 페이지 1000건으로 전량, 빈 본문이면 50건씩 나누고 겹친 행 수를 errors로.
  JSON `items`는 0건이면 `""`, 1건이면 dict. 에러와 0건 구분 불가(`searchDt` 누락·미래 월도 00/0) — 요청 인자를 코드로 보장.
  bid_no = `KWATER-{tndrPbanno}`. organization "한국수자원공사", end_date = `tndrPblancEnddt`(`-`는 없음), category = `cntrctDivNm`,
  url = `ebid.kwater.or.kr/fz?bidno=`. 취소 공고는 API에서 빠진다. 범위 밖: 사전규격·발주계획(공고번호 없음)·입찰결과.
- **수용 기준**:
  - [ ] 공통 기준 전부
  - [ ] `items`가 `""`(0건)·dict(1건)·list 모두 처리 → `test_items_shapes`(3)
  - [ ] 기준일이 전월이면 전월·당월 두 달을 요청하고 공고일로 거른다 → `test_month_span_and_cutoff_filter`
  - [ ] 마감일 `-` → end_date None, 항목은 유지 → `test_dash_deadline_is_none`
  - [ ] 달마다 한 페이지(numOfRows 1000) 1회, 빈 본문이면 50건씩 나눠 받고 겹친 행 수를 errors에(회귀 — 실측 4건 누락)
    → `test_collects_four_operations_with_params` · `test_falls_back_to_small_pages_and_reports_overlap`
- **상태**: 진행

## 비기능 요구사항
1. **인증 방식**: 해당 없음 — 외부 입력 진입점이 없는 라이브러리(API 키는 호출자가 넘긴다).
2. **공개 범위**: 해당 없음 — 공개 표면은 파이썬 API(`docs/interface.md`)뿐이다. 단 **GenericScraper는 소비자가 넘긴
   임의 URL로 요청을 보낸다** — SSRF 방어는 소비자 책임이고 그 훅을 꽂을 자리(`GenericScraper(event_hooks=...)`)는 이 패키지가 제공한다(v1.1.0).
3. **호환성**: Python 3.11+, async(httpx). 소비자 계약 변경은 `docs/CONTRACT.md` 게이트를 탄다
   (필드·인자 추가 = minor, 제거·이름 변경 = major).
4. **호출 한도**: data.go.kr 개발계정 일 1,000회(서비스별) — 소비자 BidWatch가 수집+상세를 합산 관리한다.

## 하지 않는 것 (Out of Scope)
- DB 저장·키워드 매칭·스케줄링·AI 설정 생성·캐싱 — 소비 서비스(BidWatch) 몫.
- 헤드리스 브라우저(JS 렌더링 사이트) — JSON API 모드와 소비자 AI의 내부 JSON 감지가 우선(2026-09-23 결정).
- 중소벤처24(15113191) — LINK 타입 API, 별도 인증 필요, `SmesCollector`가 같은 데이터 커버(2026-04-11 스킵, `Phase_003.md` §10).

## 미결 질문
- 공기업 API 5종(LH·한전·도로공사·수자원공사·방위사업청)이 필요한가 — BidWatch 수요 확인 후 판단(plan.md '이후 단계').
- (원칙 ②) `status`를 어떻게 할지 — 제거(계약 major) / 출처가 명시한 값만(나라장터 `ntceKindNm` 취소→`cancelled`, K-Startup `rcrt_prgs_yn`) /
  BidWatch가 `end_date`로 계산. 기본값 `ongoing`도 추정이다.
- ~~(원칙 ①) BidWatch가 지금 읽는 `extra` 키가 있는지~~ — **해소(2026-09-25 확인)**: bidwatch `frontend/src/components/notices/NoticeModal.tsx`의
  NaraExtra 12개·GeneralExtra 14개(K-Startup 8·기업마당 6) 영어 키를 읽는다. 확장 3메서드·보조금24·중소벤처 키는 읽는 곳 없음. 대응표는 plan.md Phase 006 4항.
