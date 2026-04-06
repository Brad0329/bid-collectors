# Phase Rebuilding: 코드 리뷰 기반 버그 수정 - 작업 로그

> 작성일: 2026-04-06
> 상태: 완료
> 테스트: 단위 174개 전체 통과 (수정 후 테스트 변경 불필요)
> 커밋: `c188559 fix: 코드 리뷰 반영 — 데이터 정확성 버그 수정 및 인터페이스 문서 동기화`

---

## 1. 수행한 작업

Phase 0 + Phase 1 코드 전체를 대상으로 코드 리뷰를 수행하고, 발견된 버그 및 개선사항을 수정했다.

### 1-1. 리뷰 범위

- **대상**: 전체 소스 파일 11개
- **점검 항목**:
  - interface.md 계약과의 일치 여부
  - 수집기 간 일관성
  - 런타임 버그
  - 코드 품질

### 1-2. 수정 분류

| 분류 | 건수 | 설명 |
|------|------|------|
| CRITICAL | 2 | 데이터 정확성에 직접 영향 |
| SHOULD FIX | 2 | 동작하지만 데이터 누락/오류 가능 |
| NICE TO HAVE | 2 | 코드 품질 개선 |

---

## 2. 수정 내용 상세

### 2-1. CRITICAL (데이터 정확성 영향)

#### (1) nara.py — budget=0 falsy 버그

- **문제**: `budget=budget or est_price`에서 budget이 0원(`int 0`)이면 Python falsy로 평가되어 est_price로 대체됨
- **수정 전**: `budget=budget or est_price`
- **수정 후**: `budget=budget if budget is not None else est_price`
- **영향**: 예산 0원인 공고의 금액 데이터가 잘못 표시될 수 있었음
- **근본 원인**: Python에서 `0`, `""`, `None`, `[]`, `{}` 모두 falsy. 숫자 필드에 `or` 연산자를 사용하면 유효한 0값이 무시됨

#### (2) bizinfo.py — cutoff 시간 비교 버그

- **문제**: `cutoff = datetime.now() - timedelta(days=days)`에 시간이 포함됨. days=1이고 현재 14:00이면 cutoff이 어제 14:00이 됨. 당일 생성된 항목(00:00 기준)이 cutoff보다 이전으로 판정되어 누락
- **수정 전**: `cutoff = datetime.now() - timedelta(days=days)`
- **수정 후**: `.replace(hour=0, minute=0, second=0, microsecond=0)`으로 날짜 단위로 자름
- **영향**: days=1 호출 시 당일 등록 공고가 빠짐

### 2-2. SHOULD FIX (동작하지만 데이터 누락/오류)

#### (3) subsidy24.py — status 항상 "ongoing" 문제

- **문제**: `determine_status()` 미호출, status가 하드코딩 `"ongoing"`
- **수정**: `from .utils.status import determine_status` 추가 + `status=determine_status(end_str) if end_str else "ongoing"`
- **영향**: 마감 지난 보조금24 서비스가 `"closed"`로 표시되지 않았음

#### (4) 3개 수집기 — extra dict에서 0값 누락

- **문제**: `if v` 필터링에서 `view_count=0` 등 int 0이 falsy로 제외됨
- **수정 전**: `{k: v for k, v in {...}.items() if v}`
- **수정 후**: `{k: v for k, v in {...}.items() if v is not None and v != ""}`
- **적용 파일**: nara.py, bizinfo.py, subsidy24.py 모두
- **영향**: 조회수 0, 예산 0 등 유효한 0값이 extra에서 사라짐

### 2-3. NICE TO HAVE (코드 품질)

#### (5) import time 위치 정리

- **문제**: 3개 수집기의 `health_check()` 내부에 `import time` — 동작하지만 비표준
- **수정**: 모듈 상단으로 이동
- **적용 파일**: nara.py, bizinfo.py, subsidy24.py 모두

#### (6) interface.md 문서 동기화

- **문제**: interface.md에서 `collect()`가 `@abstractmethod`로 정의되어 있지만, 실제 구현은 `_fetch()`가 abstract이고 `collect()`는 템플릿 메서드
- **수정**: interface.md에 `_fetch()` 패턴 반영, `collect()`를 템플릿 메서드로 문서화

---

## 3. 수정하지 않은 항목 (의도적 유보)

| 항목 | 유보 사유 |
|------|-----------|
| beautifulsoup4 의존성 | Phase 3 generic_scraper에서 사용 예정이므로 유지 |
| `_split_date_range` 날짜 겹침 | `collect()`의 dedup이 처리하므로 실질적 문제 없음 |
| `_request_with_retry` 이중 재시도 | httpx transport retries + application retries 중첩이지만, 나라장터 API의 불안정성을 고려해 현행 유지 |
| dates.py 분기 단순화 | 동작에 문제 없고 가독성도 나쁘지 않아 유보 |
| `clean_html_to_text` 태그 처리 | 현재 동작에 문제 보고 없어 유보 |

---

## 4. 테스트 결과

- 단위 테스트 174개 전체 통과
- 수정 후 테스트 코드 변경 불필요 — 기존 테스트가 수정된 로직을 이미 커버
- 이는 테스트가 정상 동작(0값 포함, 날짜 단위 비교 등)을 기대하고 있었고, 기존 코드가 특정 엣지 케이스에서만 실패했기 때문

---

## 5. 향후 주의점

### 5-1. Python falsy 평가 주의

- `0`, `""`, `None`, `[]`, `{}` 모두 falsy
- 숫자 필드에 `or` 사용 금지. 반드시 `is not None` 사용
- dict comprehension 필터에서 `if v` 대신 `if v is not None and v != ""` 사용

```python
# BAD
budget = budget or est_price
extra = {k: v for k, v in data.items() if v}

# GOOD
budget = budget if budget is not None else est_price
extra = {k: v for k, v in data.items() if v is not None and v != ""}
```

### 5-2. 날짜 비교 주의

- `datetime.now() - timedelta(days=N)`은 시간이 포함됨
- 날짜 단위 비교가 필요하면 반드시 시간을 자를 것

```python
# BAD — 14:00에 호출하면 cutoff이 어제 14:00
cutoff = datetime.now() - timedelta(days=days)

# GOOD — cutoff이 어제 00:00:00
cutoff = (datetime.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
```

### 5-3. 새 수집기 추가 시 체크리스트

1. status에 `determine_status()` 적용 (하드코딩 금지)
2. extra dict 필터에 `if v is not None and v != ""` 사용
3. `import time`은 모듈 상단에 배치
4. 숫자 필드에 `or` 대신 `if ... is not None` 사용
