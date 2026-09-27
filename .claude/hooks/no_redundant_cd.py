"""위치를 옮기는 접두사(`cd … &&`, `git -C <루트>`)가 붙은 셸 호출을 막는다 (PreToolUse 훅).

**왜 훅인가**: 이 함정은 CLAUDE.md와 플레이북에 두 곳이나 적혀 있는데도 kanadic 2026-08-19
세션에서 **154회** 반복됐다. 문서로 막히지 않는 것이 증명됐으므로 구조로 강제한다
(CLAUDE.md: "로그에 적은 교훈은 저절로 다음 코드에 옮겨지지 않는다").

**무엇이 문제인가**: 승인 규칙은 명령을 조각으로 나눠 접두사로 검사한다. 맨 앞에 무엇이 붙거나
경로 표기가 달라지면 어떤 규칙에도 안 걸려 **매번 확인 창이 뜬다.** 세 갈래를 막는다:

  ① `cd <저장소 루트> && …` — 작업 디렉토리는 이미 루트이고 호출 사이에 유지되므로 하는 일이 없다.
     변형(vanasso.kr 2026-09-06 — 10세션 1,000건 중 168건이 통과했던 형태): `cd <루트> 2>/dev/null; cmd` ·
     `cd <루트> cmd`(구분자 없음 — bash는 "too many arguments"로 죽는다) · `cd /c/Users/…`(MSYS 경로).
  ② `cd <다른 디렉토리> && …` (bid-collectors 2026-09-26 — 서브에이전트가 `cd scripts/_tmp/x && ../../../.venv/…`로
     20여 건, 건당 최대 422초 대기). `cd` 자체가 허용돼 있어도 **뒤 명령의 경로가 `../../..`로 바뀌어**
     `.venv/Scripts/python.exe scripts/*` 같은 규칙에 안 걸린다. 루트에서 경로를 인자로 넘기면 된다.
  ③ `git -C <저장소 루트> …`(읽기·쓰기 모두) — ①과 같은 부류(같은 날 72건, 읽는 `diff` 1건이 19,833초).
     `git diff*`·`git add *`류 규칙이 `git -C …`에 안 걸린다. 예전엔 쓰는 git(add/commit/mv/restore)만
     막고 읽는 -C는 드리프트 확인용으로 열었으나 2026-09-27 뒤집었다(노하우_승인_대기_최소화 [H16]).

**막지 않는 것**:
  - `cd app` / `cd ..` 처럼 **디렉토리만 바꾸는 단독 호출** (정말 옮겨 가서 일해야 할 때의 길)
  - 다른 저장소(bidwatch 등)를 가리키는 `git -C <다른 경로>`

**판정 패턴은 `scripts/measure_approvals.py`에서 import한다** — 같은 규칙이 두 곳에 있으면
조용히 어긋난다(CLAUDE.md '같은 규칙이 두 곳 이상' 조항). 그쪽 패턴은 저장소 경로를
하드코딩하지 않고 파일 위치에서 계산하므로 프로젝트 간 복사가 그대로 된다.
import에 실패하면 **막지 않고 통과**시킨다 — 훅 결함이 도구 사용을 봉쇄하면 안 된다.
"""

import sys
from pathlib import Path

from hook_io import deny, read_command

ROOT = Path(__file__).resolve().parents[2]

REASON_ROOT = (
    "작업 디렉토리는 이미 저장소 루트이고 호출 사이에 유지된다 — 이 `cd`는 하는 일이 없다.\n"
    "  그런데 맨 앞에 `cd`가 붙으면 **어떤 허용 규칙에도 안 걸려 매번 승인을 묻는다.**\n"
    "  → `cd ...&&` 를 떼고 명령만 그대로 부를 것."
)
REASON_OTHER = (
    "`cd <디렉토리> && …`는 막는다 — 옮겨 간 뒤의 명령은 경로가 `../../.venv/…`처럼 바뀌어\n"
    "  허용 규칙에 안 걸리고 **매번 승인을 묻는다**(bid-collectors 2026-09-26 실측: 건당 최대 422초).\n"
    "  → 저장소 루트에서 경로를 인자로 넘긴다: `.venv/Scripts/python.exe scripts/_tmp/x/run.py`, `ls scripts/_tmp/x`.\n"
    "  → 스크립트가 제 폴더 기준으로 돌아야 하면 스크립트 안에서 `Path(__file__).parent`를 쓴다.\n"
    "  → 다른 저장소(bidwatch 등)의 파일을 보려면 셸 대신 `Read`/`Grep`/`Glob` 도구에 절대경로를 준다."
)
REASON_GIT_C = (
    "`git -C <저장소 루트>`는 하는 일이 없다 — 이미 루트에 있다.\n"
    "  그런데 `-C`가 붙으면 `git diff*`·`git log*`·`git add *` 같은 허용 규칙에 안 걸려 **매번 승인을 묻는다**\n"
    "  (bid-collectors 2026-09-26 실측: 읽는 `git -C <루트> diff` 1건 19,833초).\n"
    "  → `-C <경로>`를 떼고 `git diff …`로 부를 것. (다른 저장소를 가리키는 `-C`는 막지 않는다.)"
)


def blocked(command: str) -> str | None:
    """막아야 하면 그 이유, 아니면 None. 테스트가 이 함수를 직접 부른다.

    패턴을 못 읽으면 ImportError를 그대로 올린다 — main이 통과로 처리하며 이유를 남긴다.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from measure_approvals import REDUNDANT_CD_COMMAND, REDUNDANT_GIT_C, split_segments

    command = command or ""
    if REDUNDANT_CD_COMMAND.match(command):
        return REASON_ROOT
    segs = split_segments(command)
    if len(segs) > 1 and segs[0].startswith("cd "):
        return REASON_OTHER
    if REDUNDANT_GIT_C.search(command.split("\n", 1)[0]):
        return REASON_GIT_C
    return None


def main() -> int:
    command = read_command("no_redundant_cd")
    if command is None:
        return 0
    try:
        reason = blocked(command)
    except Exception as e:
        # 패턴 소스를 못 읽었다고 도구를 막지 않는다. 통과시키되 이유를 남긴다.
        print(f"no_redundant_cd: 판정 실패로 통과시킨다 ({e})", file=sys.stderr)
        return 0
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
