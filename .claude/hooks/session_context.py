r"""세션이 시작될 때 plan.md의 현재 Phase와 이어 읽을 파일을 컨텍스트에 주입한다 (SessionStart 훅).

**왜 훅인가**: 종전 CLAUDE.md '새 세션 시작 시'는 "plan.md를 읽는다 / 진행 중 Phase 로그를 읽는다"
두 규칙이었다. 기억에 의존하는 규칙이고, 어겨도 아무 증상이 없다(구버전 이해로 그냥 구현한다).
주입은 기계적으로 할 수 있으므로 구조로 옮기고 CLAUDE.md에서는 한 줄로 줄였다.

**무엇을 주입하나**:
  - plan.md 'Phase 체크리스트'의 최상위 Phase 줄 전부(완료/전체 건수와 함께 — 자르지 않는다)
  - 첫 미완료 Phase = 현재 Phase, 그 번호의 `work_log/Phase_NNN.md`가 있으면 그 경로
  - '사용자 실테스트 대기' 절의 항목(플레이스홀더 `<...>` 제외)
  plan.md 전문을 싣지는 않는다 — 길어지면 매 세션 컨텍스트를 먹는다. 읽으라고 지시만 한다.

**주입하지 않는 경우에도 한 줄은 낸다** — 이 훅의 출력이 세션 첫머리에 안 보이면 훅이 죽은 것이다
(2026-08-22 훅 4종이 cp949로 조용히 죽어 있던 실사례. 죽은 훅과 "할 말이 없는 훅"을 구분한다).
  - 미초기화 템플릿(CLAUDE.md에 초기화 블록이 남아 있음): 초기화가 먼저라고만 알린다
  - plan.md 없음: 없다는 사실을 알린다

**훅이 고장 나면 세션을 막지 않는다** — 이유를 stderr에 남기고 exit 0.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from hook_io import add_context

ROOT = Path(__file__).resolve().parents[2]

INIT_MARKER = "템플릿 초기화 모드"
PHASE_LINE = re.compile(r"^- \[( |x|X)\] (Phase [^\s:]+)(.*)$")
PHASE_NUMBER = re.compile(r"Phase (\d{3})$")
HEADING = re.compile(r"^## +(.*)$")


def _section(lines: list[str], title_part: str) -> list[str]:
    """`## …title_part…` 절의 본문 줄들. 절이 없으면 빈 목록."""
    body: list[str] = []
    inside = False
    for line in lines:
        m = HEADING.match(line)
        if m:
            if inside:
                break
            inside = title_part in m.group(1)
            continue
        if inside:
            body.append(line)
    return body


def build_context(root: Path) -> str:
    """주입할 문장을 만든다. 테스트가 이 함수를 직접 부른다."""
    claude_md = root / "CLAUDE.md"
    if claude_md.exists() and INIT_MARKER in claude_md.read_text(encoding="utf-8"):
        return "[session_context] 미초기화 템플릿이다 — CLAUDE.md 초기화 체크리스트가 먼저다. plan.md 주입은 생략한다."

    plan = root / "work_log" / "plan.md"
    if not plan.exists():
        return "[session_context] work_log/plan.md가 없다 — 현재 Phase를 알 수 없다. 사용자에게 확인할 것."

    lines = plan.read_text(encoding="utf-8").splitlines()

    phases = [m for m in (PHASE_LINE.match(l) for l in _section(lines, "Phase 체크리스트")) if m]
    done = sum(1 for m in phases if m.group(1).lower() == "x")
    current = next((m for m in phases if m.group(1) == " "), None)

    out = ["[session_context] work_log/plan.md 요약 — 작업 전에 plan.md 전문을 Read로 읽을 것."]
    out.append(f"Phase {done}/{len(phases)} 완료:")
    out += [f"  {m.group(0)}" for m in phases]

    if current is None:
        out.append("현재 Phase: 미완료 Phase가 없다 — 다음 단계를 사용자에게 확인할 것.")
    else:
        out.append(f"현재 Phase: {current.group(2)}{current.group(3)}")
        num = PHASE_NUMBER.search(current.group(2))
        log = root / "work_log" / f"Phase_{num.group(1)}.md" if num else None
        if log is not None and log.exists():
            out.append(f"★ 이 Phase의 로그가 있다 — work_log/{log.name}도 읽을 것(이전 결정과 실패한 접근).")
        else:
            out.append("이 Phase의 로그 파일은 없다(실패한 접근·버린 대안이 아직 없다는 뜻).")

    waiting = [l for l in _section(lines, "사용자 실테스트 대기")
               if l.lstrip().startswith("-") and not l.lstrip().startswith("- <")]
    if waiting:
        out.append(f"사용자 실테스트 대기 {len(waiting)}건 — 확인 전 완료 선언 금지:")
        out += [f"  {l.strip()}" for l in waiting]

    return "\n".join(out)


def main() -> int:
    try:
        text = build_context(ROOT)
    except Exception as e:
        # 주입 실패로 세션을 막지 않는다. 조용히 넘기지 않고 이유를 남긴다.
        print(f"session_context: plan.md 요약을 만들지 못했다 ({e})", file=sys.stderr)
        return 0
    add_context("SessionStart", text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
