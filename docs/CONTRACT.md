# bid-collectors 소비자 계약 — 변경 결정 기록

> 이 패키지에는 DB 스키마가 없다. 템플릿의 '데이터 모델/스키마 변경 게이트'는 **소비자 계약**에 건다 —
> 되돌리기 비싼 것은 테이블이 아니라 **BidWatch가 기대하는 모양**이기 때문이다(2026-09-23 이식 시 결정).
>
> **계약의 범위**: `Notice` · `CollectResult` · `BaseCollector`(`collect`·`health_check`·`fetch_detail`·생성자 키 규칙) ·
> `ScraperConfig`(BidWatch의 AI가 이 스키마대로 config를 만든다) · 공개 export(`bid_collectors/__init__.py`의 `__all__`) ·
> 수집기별 `bid_no` 형식(BidWatch가 `(source, bid_no)`로 upsert한다 — 형식이 바뀌면 같은 공고가 중복 저장된다).
>
> **변경 절차**: ① 변경안(무엇을·왜·영향 범위·버전)을 아래 '변경 이력'에 먼저 기록 → ② 사용자 확인 →
> ③ 코드 반영 + `docs/interface.md` 갱신 **— 이 문서는 bidwatch 저장소에도 같은 파일이 있다. 고치면 양쪽 다**
> → ④ `pyproject.toml`·`bid_collectors/__init__.py`의 버전을 함께 올린다.
>
> **버전 규칙**: 필드·선택 인자 **추가**(기본값 있음) = minor / 필드 제거·이름 변경·타입 변경·bid_no 형식 변경 = **major**.
> `extra` 안의 키는 계약 밖이다(수집기가 자유롭게 넣고 BidWatch는 JSONB로 통째 저장).
>
> 이 문서는 "결정과 이유"를, 코드(`bid_collectors/models.py`·`base.py`·`generic_scraper.py`)는 "현재 상태"를 담당한다.
> 둘이 어긋나면 코드가 정답이고 이 문서와 `interface.md`를 고친다.

## 설계 원칙
- 수집기는 **가져오기만** 한다 — 저장·매칭·스케줄·캐시는 소비자. 새 요구가 소비자 쪽 일이면 여기서 받지 않는다.
- 동기 대신 **async(httpx)** — BidWatch 워커가 여러 출처를 병렬로 부른다.
- 부분 실패는 예외가 아니라 `CollectResult.errors`/`is_partial`로 알린다 — 앞서 모은 공고를 버리지 않는다
  (현재 위반 다수 — `work_log/plan.md` Phase 004).
- 출처별 추가 필드는 `Notice`에 필드를 늘리지 않고 `extra`에 둔다(출처마다 필드가 달라 표준화 비용이 크다).

## 결정
### `_fetch()` 템플릿 메서드 — 서브클래스는 `collect()`를 오버라이드하지 않는다
- **결정**: `_fetch()`가 `(notices, pages_processed)`를 반환하고 `collect()`가 중복 제거·계측·예외 포착을 한다.
- **이유**: 중복 제거·에러 래핑이 수집기마다 달라지는 것을 막는다. kwargs에 페이지 수를 싣던 초기 설계는
  kwargs가 복사돼 동작하지 않았다(`work_log/Phase_002.md` §9-5).

### 나라장터 확장 메서드는 `list[Notice]`를 반환한다
- **결정**: `collect_awards`·`collect_contracts`·`collect_pre_specs`는 `CollectResult`가 아니라 리스트를 반환(2026-04-11).
- **결과**: 에러가 예외로 호출자에게 간다. 바꾸면(→ `CollectResult`) BidWatch 연쇄 수집 코드가 깨지므로 **major**.

### 나라장터 `fetch_detail`은 None
- **결정**: data.go.kr에 단건 조회·사업개요 API가 없어 g2b 스크래핑을 제거하고 None 반환, 상세 필드는 수집 시 extra에(2026-04-13).
- **근거**: `work_log/Phase_003_detail.md`.

## 변경 이력 (최신이 위)
| 날짜 | 변경안 (무엇을, 왜, 영향 범위, 버전) | 사용자 확인 | 반영 |
|---|---|---|---|
| (예정) | v1.1.0 신뢰성 — 페이지 실패·쿼터 초과·max_pages 절단을 `errors`/`is_partial`로 보고(기존 필드, 의미만 채움), `GenericScraper`·`create_client`에 요청 검사 훅 선택 인자 추가 — minor | 2026-09-23 bidwatch 세션 합의 | Phase 004 |
| 2026-04-13 | 나라장터 `fetch_detail` 스크래핑 제거 → None | ✅ | `1a037e3` |
| 2026-04-11 | v1.0.0 — `GenericScraper`/`ScraperConfig` export, 나라장터 확장 3메서드, `fetch_detail` 추가 | ✅ | `3f21d98`·`714420e` |
| 2026-04-06 | 초기 계약 — `Notice`·`CollectResult`·`BaseCollector._fetch` 템플릿 메서드 | ✅ | `c61291e`·`c188559` |

## 알려진 문서 불일치 (Phase 004에서 `interface.md` 갱신 시 함께 정리)
- `interface.md`가 `fetch_detail`·`ScraperConfig`를 다루지 않는다. §4의 `GenericScraper.__init__`이 dict만 받는 것처럼
  적혀 있으나 실제로는 `ScraperConfig | dict`이고 dict는 즉시 검증된다.
- §5 환경변수 표에 미구현 수집기(LH·한전·도로공사·수자원공사·방위사업청·중소벤처24)가 있다.
- **bidwatch 쪽 `interface.md`가 이미 어긋나 있다** — `BaseCollector`를 `_fetch` 이전 형태(`collect`가 abstract)로 적고 있다
  (2026-04-06 `c188559`에서 이쪽만 고쳐짐). 다음 갱신 때 이쪽 판으로 맞춘다.
