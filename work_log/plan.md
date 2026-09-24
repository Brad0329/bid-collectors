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
  게시판(GenericScraper)에서 가져와 표준 `Notice`/`CollectResult`로 돌려주는 파이썬 패키지(v1.1.0).
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
- [ ] Phase 005: 항목 수준 조용한 실패 제거 — 필드 이상 1건이 결과를 지우거나 조용히 사라지지 않게 (F-001·F-003~F-007) — **계약 불변(errors 내용만 늘어남) → patch**
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

## 이후 단계 (Phase 번호 미발급 — 착수 시 번호를 받고 위 체크리스트로 옮긴다)

- **v1.2 JSON API 모드** — 설계 `docs/generic_scraper.md` §8-1(`api_mode="json"`). 그 뒤 전용 사이트:
  창조경제혁신센터 7개 지역 · 부산창업포탈 · 한국예탁결제원 · 창조경제혁신센터 지원사업(lets_portal 전용 수집기 — `docs/dev_reference.md` §8).
  같은 문서의 후보: §8-2 `bid_no_regex`(URL의 안정적 ID로 bid_no) · §8-3 상세 페이지 스크래핑.
- **공기업 API 5종 — 필요성 판단 후** (구 plan.md "Phase 3", data.go.kr 서비스 ID까지 조사 완료):
  LH 입찰공고 15021183(+ 계약 15021184·발주계획 15042795·사전규격 15042796·개찰 15057180) · 한전 전자입찰계약 15148223 ·
  도로공사 전자조달 계약공개 15128076 · 수자원공사 전자조달 입찰공고 15101635 · 방위사업청 입찰공고 15002040(+ 결과 15002018·
  조달계획 15002017·계약 15002019·코드 15002020). 나라장터에 안 올라오는 자체 입찰건 확보가 가치. 구현은 Phase 002 패턴 복제.
- **v1.3 호출 한도 대응** — `CollectResult`에 data.go.kr 호출 수 반환(일 1,000회 한도를 BidWatch가 합산하기 위함, 필드 추가 = minor) ·
  429 지수 백오프(현재 나라장터만 30초 고정 3회).
- **헤드리스 브라우저는 하지 않는다**(2026-09-23) — JS 렌더링 사이트는 JSON 모드·소비자 AI의 내부 JSON 감지가 우선.
- 구 plan.md "Phase 4 품질+운영"의 나머지: 로깅 표준화 · 수집기별 필드 매핑표 문서 · BidWatch 워커에서 안정 동작 확인(→ BidWatch 자동 수집 Phase 몫).
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

**미정**
- 공기업 API 5종의 필요성 — BidWatch 수요 확인 후

## 사용자 실테스트 대기
- **BidWatch 연동(bidwatch 세션 몫)** — ① 정기·시험 수집의 `GenericScraper(...)` 3곳(`tasks/collect_scraper.py`·`services/scraper_ai.py`·
  `routers/sources.py`)에 `event_hooks={"request": [guard_request]}` 꽂기 ② v1.1부터 `is_partial`/errors가 채워진다 —
  보조금24 정기 수집은 이제 절단 보고 대신 정상 건수(1일 24건 수준)로 온다, GenericScraper는 max_pages 절단이 errors로 온다(기본 3페이지)
  ③ bidwatch venv에서 `pip install -e` 재실행(메타데이터가 1.0.0으로 남아 있음, 코드는 editable이라 이미 1.1.0)

## 보류 항목 (나중에 할 것)

> 조사만 하고 미룬 것을 여기 남긴다. **다시 조사하지 않아도 되도록 실측 결과와 근거까지** 적는다.

- **debt-audit 2026-09-24 (5afbc99 반영본)** — 1군(GenericScraper 정렬 가정·헛통과 테스트·공통 계약 테스트)은 Phase 005에 흡수(위 4~6). 나머지는 **결정 대기**:
  - 실측 필요: **기업마당 정렬 가정**(`bizinfo.py:81-84` "마지막 3건 전부 오래됨"에서 멈춤 — 5afbc99와 같은 유형, 실제 목록 순서 역전 수 미측정) ·
    K-Startup `fetch_detail`의 `cond[pbanc_sn::EQ]`가 실제로 먹는지(응답 ID ≠ 요청 ID 검사 없음 — 무시되면 다른 공고 본문을 조용히 반환)
  - 저위험 결함: K-Startup odcloud `code<0` 미검사·`totalCount` 사용(보조금24는 `matchCount`) / 보조금24 `신청기한` 기간 형식이면 시작일이 end_date로 /
    기업마당 날짜 형식 변경 시 cutoff 필터가 조용히 꺼짐 / 기업마당·K-Startup·보조금24 total이 문자열·null이면 TypeError로 전체 손실 /
    GenericScraper health_check·페이지 오류 메시지 키 마스킹 누락 / 알리오 `old_pages` 전부 건너뛴 페이지에서 리셋(주석은 "세지 않는다")
  - 일반 트랙: GenericScraper status를 게시일로 판정(어제 공고가 closed) · interface.md GenericScraper `collect(days=30)`인데 실제 기본 1 ·
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
