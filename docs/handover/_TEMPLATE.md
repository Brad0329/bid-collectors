# handover v<버전> — bid-collectors → BidWatch (작성 YYYY-MM-DD)

> **규칙(2026-09-25 사용자 결정)**: bid-collectors 세션은 bidwatch 폴더의 파일을 수정하지 않는다. 버전 작업을 끝내면 이 문서를
> `docs/handover/v<버전>.md`로 만들어(한 버전에 하나) 변경 상황과 bidwatch가 할 일을 넘긴다. 반영은 bidwatch 세션이 하고,
> 끝나면 §5를 채운다(그쪽 세션이 이 파일을 고쳐도 된다). 이 템플릿을 복사해 쓰고, 해당 없는 절은 "없음"이라고 적는다 — 지우지 않는다.

## 1. 무엇이 바뀌었나
- 버전 · 커밋 · Phase(plan.md 항목):
- **계약**(`Notice`·`CollectResult`·시그니처·`bid_no` 형식·공개 export): 불변 / 변경 — 무엇이 어떻게(`docs/CONTRACT.md` 변경 이력 행)
- **동작**: 소비자가 받는 값·`errors` 메시지 종류·`is_partial` 빈도가 어떻게 달라지나(실측 수치와 표본)
- **`docs/interface.md` diff**: bidwatch 쪽 같은 파일에 그대로 반영할 부분(절 번호·줄)

## 2. bidwatch가 해야 할 일
| # | 파일(bidwatch 기준 경로) | 무엇을 | 왜 | 필수/선택 |
|---|---|---|---|---|
| 1 | | | | |

## 3. 검증 방법
- `pip install -e <bid-collectors 체크아웃>` 재실행 필요 여부(메타데이터 버전 ↔ `bid_collectors.__version__`)
- `backend/.venv/Scripts/python.exe -m pytest backend/tests` — 기대: 전부 통과 / 바뀌는 기대값이 있으면 어느 테스트인지
- 실테스트 항목: 무엇을 어떻게 돌려 무엇을 보면 되는가(기대값·표본 수)

## 4. 되돌리기
- 이전 버전 커밋/태그: — editable 설치라 bid-collectors 체크아웃을 그 커밋으로 바꾸면 돌아간다. bidwatch 쪽 수정을 되돌려야 하는 경우:

## 5. 반영 확인 (bidwatch 세션이 채운다)
- [ ] 반영 커밋: · 날짜: · 남은 것:
