# bid-collectors 요구사항 (단일 원본)

> 기능 요구사항 + 수용 기준의 단일 원본. **요구사항이 바뀌면 대화로 합의만 하지 말고 이 파일을 즉시 갱신.**
> 완료 판정은 항상 이 문서를 기준으로 한다(에이전트를 쓰든 직접 대조하든).
> **새 F-번호는 CLAUDE.md의 '요구사항 ID 대장'에서 받아간다. 번호 재사용 금지.**
>
> **F-001~F-009는 2026-09-23 v1.0.0 구현에서 역작성했다** — 코드의 현재 동작을 기준으로 적었고,
> 각 기준 뒤에 대응 테스트를 `→ 테스트명`(파일은 `tests/test_<수집기>.py`)으로, 없으면 `→ 테스트 없음`으로 표시했다.
> `(현재 실패)` = 픽스처 날짜가 2026-04로 고정돼 실행 시점 cutoff에 걸리는 기존 결함(plan.md Phase 004에서 고친다).
> 테스트 없는 기준은 그 기능을 다음에 건드릴 때 테스트부터 붙인다.
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
- **알려진 위반 (v1.1에서 고침)**: 수집기 내부에서 페이지 요청이 실패하면 로그만 남기고 `break`해
  `errors`가 비고 `is_partial=False`로 돌아온다 — 소비자가 "장애"와 "공고 없음"을 구분 못 한다(조용한 실패).
  `max_pages` 상한에 걸려 멈춰도 알리지 않는다(조용한 절단). → `work_log/plan.md` Phase 004.

## 기능 요구사항

### F-001: 나라장터 입찰공고 수집 (`NaraCollector.collect`)
- **설명**: 조달청 입찰공고 API(용역·물품·공사 3개 서비스)를 날짜 범위로 조회. 7일 단위로 범위를 나누고
  100건/페이지로 totalCount까지 넘긴다. 429는 30초 대기 후 3회 재시도. `bid_types` kwargs로 서비스 선택.
- **수용 기준**:
  - [x] days를 7일 단위 범위(`yyyyMMdd0000`~`yyyyMMdd2359`)로 나눈다 → `TestSplitDateRange` 6건
  - [x] `resultCode != 00` 응답은 `ValueError` → `test_error_response_raises_valueerror`
  - [x] bid_no = `{용역|물품|공사}-{bidNtceNo}-{bidNtceOrd}`(차수 없으면 생략) → `test_bid_no_format`·`test_bid_no_without_ord`
  - [x] budget = 배정예산, 없으면 추정가격(0원도 유효값) → `test_budget_and_est_price`
  - [x] 공고첨부 1~10 + 규격서 1~10을 attachments로 병합 → `test_attachments_parsing`
  - [x] 여러 페이지를 totalCount까지 넘긴다 → `test_multi_page_pagination`
  - [x] 429를 받으면 재시도해 성공 응답을 쓴다 → `test_429_retry_logic` · 재시도 소진 시 0건 → `test_429_exhausts_retries`
  - [ ] 한 서비스의 `resultCode != 00`(쿼터 초과 포함)이 앞서 수집한 서비스 결과를 버리지 않는다 → **미충족**(Phase 004)
- **상태**: 완료 (2026-04-06, 상세 필드 보강 2026-04-13)

### F-002: 나라장터 확장 — 낙찰·계약·사전규격 (`collect_awards` / `collect_contracts` / `collect_pre_specs`)
- **설명**: 입찰공고와 같은 루프(`_fetch_extended`)로 세 API를 조회해 **`list[Notice]`를 바로 반환**한다
  (`CollectResult` 아님 — `collect()` 계약 밖의 확장 메서드). bid_no 접두사 `낙찰-`·`계약-`·`사전규격-`.
  사전규격 API만 `ServiceKey`(대문자 S). BidWatch는 사전규격을 `nara_prespec` 출처로 연쇄 수집한다.
- **수용 기준**:
  - [ ] 사전규격: bid_no = `사전규격-{type}-{bfSpecRgstNo}`, 의견마감일이 end_date, 규격서 1~5가 attachments → 테스트 없음
  - [ ] 낙찰: status가 항상 `closed`, extra에 낙찰자·낙찰률·참가자 수 → 테스트 없음
  - [ ] 계약: bid_no = `계약-{type}-{dcsnCntrctNo}` → 테스트 없음
  - [ ] API 에러 응답 시 예외가 호출자에게 그대로 전파된다(현재 동작 — 부분 결과 없음) → 테스트 없음
- **상태**: 완료 (2026-04-11, 실 API 1일분 검증: 낙찰 15·계약 6,718·사전규격 322건 — `work_log/Phase_003.md` §11)

### F-003: K-Startup 사업공고 수집 (`KstartupCollector`)
- **설명**: odcloud 형식 JSON. 접수시작일이 cutoff(자정 절삭) 이전이면 제외. `only_ongoing=True`(기본)면
  모집중만 서버 필터. 상태는 API의 `rcrt_prgs_yn`으로 판정. content 500자 절단(전문은 `fetch_detail`).
- **수용 기준**:
  - [x] bid_no = `KSTARTUP-{pbanc_sn}` → `test_bid_no_format` (현재 실패)
  - [x] cutoff 이전 접수시작 공고는 None → `test_cutoff_filtering_old_item_returns_none`
  - [x] url = 상세 → 신청 → 안내 URL 순 폴백 → `test_url_fallback_chain_*` (현재 실패)
  - [x] `only_ongoing=True`면 `cond[rcrt_prgs_yn::EQ]=Y`를 보낸다 → `test_only_ongoing_param`
  - [x] 여러 페이지 수집 → `test_multi_page_pagination` (현재 실패)
  - [x] HTTP·네트워크 오류 시 예외 없이 반환 → `test_http_error_graceful`·`test_network_error_graceful` (단 errors 비어 있음 — 위 '알려진 위반')
- **상태**: 완료 (2026-04-06)

### F-004: 기업마당 지원사업 수집 (`BizinfoCollector`)
- **설명**: 별도 키 `BIZINFO_API_KEY`. API에 날짜 필터가 없어 전체를 받으며 `creatPnttm` 기준 클라이언트 필터.
  정렬이 보장되지 않아 페이지 마지막 3건이 모두 cutoff 이전일 때만 조기 종료.
- **수용 기준**:
  - [x] bid_no = `BIZINFO-{pblancId}` → `test_bid_no_format` (현재 실패)
  - [x] `reqstBeginEndDe`("A ~ B")에서 시작·종료일을 뽑는다 → `test_date_parsing_period_format` (현재 실패)
  - [x] cutoff 이전 생성 공고는 None, 생성일 없으면 통과 → `test_old_item_returns_none`·`test_empty_creatpnttm_not_filtered`
  - [x] 인쇄본·첨부 두 파일을 attachments로 → `TestParseAttachments` 5건
  - [x] 여러 페이지 수집 → `test_multi_page_pagination` (현재 실패)
- **상태**: 완료 (2026-04-06)

### F-005: 보조금24 공공서비스 수집 (`Subsidy24Collector`)
- **설명**: odcloud gov24 v3. `cond[수정일시::GTE]`로 서버 필터. 한국어 필드명. 서비스 15113968 별도 활용 신청 필요.
  `only_business=True`면 키워드(`BUSINESS_KEYWORDS`)로 기업 대상만 남긴다(휴리스틱).
- **수용 기준**:
  - [x] bid_no = `GOV24-{서비스ID}`, 서비스ID·서비스명 중 하나라도 없으면 None → `test_bid_no_format`·`test_missing_*_returns_none`
  - [x] url = 상세조회URL, 없으면 gov.kr 기본 URL → `test_url_fallback_to_gov_kr`
  - [x] 신청기한이 end_date, 마감 지나면 closed → `test_end_date_from_deadline` · 통합 매핑 `test_full_item_mapping` (현재 실패)
  - [x] `only_business=True`면 기업 키워드 없는 항목 제외 → `test_only_business_filter`
  - [x] API 에러 코드(`code < 0`) 시 예외 없이 반환 → `test_api_error_response` (단 errors 비어 있음)
  - [ ] cutoff를 자정으로 절삭한다(다른 수집기와 같게) → **미충족**(`subsidy24.py:38`, Phase 004)
- **상태**: 완료 (2026-04-06)

### F-006: 중소벤처기업부 사업공고 수집 (`SmesCollector`)
- **설명**: XML, **HTTP**(HTTPS 아님) 엔드포인트. `startDate`/`endDate` 서버 필터. 예산은 `suptScale` 첫 숫자.
- **수용 기준**:
  - [x] bid_no = `MSS-{itemId}` → `test_bid_no_format`
  - [x] 예산을 지원규모 문자열에서 추출, 없으면 None → `test_budget_parsing_from_suptscale`·`test_budget_none_when_missing`
  - [x] fileName/fileUrl 쌍 추출(이름 부족 시 `첨부파일N`) → `TestExtractAttachments` 3건
  - [x] content 500자 절단 → `test_content_truncation_500_chars`
  - [x] HTTP 오류·XML 에러 응답 시 0건 반환 → `test_http_error_returns_empty`·`test_xml_error_response_returns_empty` (단 errors 비어 있음 — v1.1에서 이 테스트의 기대가 바뀐다)
- **상태**: 완료 (2026-04-06)

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
  - [x] 2페이지 요청 실패 시 1페이지 결과는 남긴다 → `test_http_error_partial_collect` (단 errors·is_partial로 알리지 않음)
  - [x] `verify_ssl=False`가 실제 transport에 적용된다 → `test_http::test_verify_false_disables_certificate_check` (2026-09-23 `152f930`)
  - [ ] 1페이지 요청이 실패하면 0건과 함께 errors에 원인이 담긴다 → **미충족**(Phase 004)
  - [ ] max_pages에 도달해 멈추면 잘렸다는 사실을 알린다 → **미충족**(Phase 004)
  - [ ] 소비자가 요청 검사 훅(SSRF 방어)을 주입할 수 있다 → **미충족**(Phase 004)
- **엣지케이스**: grid_selector가 없으면 0건 / 제목 없는 행·파싱 예외 행은 건너뜀(debug 로그) / `skip_no_date=False`면 날짜 없이 수집.
- **상태**: 완료 (2026-04-11) — 조용한 실패·절단·훅 주입은 Phase 004

### F-008: 상세 조회 (`fetch_detail(bid_no)`)
- **설명**: 목록 수집에 없는 정보를 공고 1건 단위로 보충한다. 결과 캐싱은 소비자 몫.
  K-Startup만 구현(`cond[pbanc_sn::EQ]` 단건 조회 → content 전문 + 대상·신청방법 등 dict).
  나라장터는 API가 단건 조회·사업개요를 지원하지 않아 None — 대신 수집 시점에 extra를 채운다(`work_log/Phase_003_detail.md`).
- **수용 기준**:
  - [ ] K-Startup: 존재하는 bid_no면 content 전문이 든 dict, 없는 번호·오류면 None → 테스트 없음
  - [ ] 나라장터·기업마당·보조금24·중소벤처기업부·GenericScraper는 None → 테스트 없음
- **상태**: 완료 (2026-04-11, 나라장터 스크래핑 제거 2026-04-13)

### F-009: 공통 유틸 — 날짜 파싱·HTML 정리·상태 판정·HTTP 클라이언트
- **수용 기준**:
  - [x] `parse_date`: `YYYY-MM-DD`·`.`·`/`·`YYYYMMDD(HHMM)`·2자리 연도·`YYYY년 M월 D일`·기간 문자열의 시작일, 실패 시 None → `test_dates.py` 19건
  - [x] `clean_html_to_text`: 태그 제거·블록 태그 줄바꿈·엔티티 복원·연속 줄바꿈 2개로 → `test_text.py`
  - [x] `determine_status`: 마감일이 오늘 이후면 ongoing, 지났으면 closed, 파싱 불가면 ongoing → `test_status.py`
  - [x] `create_client`: User-Agent·타임아웃 기본값, 리다이렉트 추적, transport 인자(verify 등)를 transport로 전달 → `test_http.py`
- **상태**: 완료

## 비기능 요구사항
1. **인증 방식**: 해당 없음 — 외부 입력 진입점이 없는 라이브러리(API 키는 호출자가 넘긴다).
2. **공개 범위**: 해당 없음 — 공개 표면은 파이썬 API(`docs/interface.md`)뿐이다. 단 **GenericScraper는 소비자가 넘긴
   임의 URL로 요청을 보낸다** — SSRF 방어는 소비자 책임이지만 그 훅을 꽂을 자리는 이 패키지가 제공해야 한다(Phase 004).
3. **호환성**: Python 3.11+, async(httpx). 소비자 계약 변경은 `docs/CONTRACT.md` 게이트를 탄다
   (필드·인자 추가 = minor, 제거·이름 변경 = major).
4. **호출 한도**: data.go.kr 개발계정 일 1,000회(서비스별) — 소비자 BidWatch가 수집+상세를 합산 관리한다.

## 하지 않는 것 (Out of Scope)
- DB 저장·키워드 매칭·스케줄링·AI 설정 생성·캐싱 — 소비 서비스(BidWatch) 몫.
- 헤드리스 브라우저(JS 렌더링 사이트) — JSON API 모드와 소비자 AI의 내부 JSON 감지가 우선(2026-09-23 결정).
- 중소벤처24(15113191) — LINK 타입 API, 별도 인증 필요, `SmesCollector`가 같은 데이터 커버(2026-04-11 스킵, `Phase_003.md` §10).

## 미결 질문
- 공기업 API 5종(LH·한전·도로공사·수자원공사·방위사업청)이 필요한가 — BidWatch 수요 확인 후 판단(plan.md '이후 단계').
