"""Bash 호출이 `cd <저장소 루트> && ...` 형태면 막는다 (PreToolUse 훅).

**왜 훅인가**: 이 함정은 CLAUDE.md와 플레이북에 두 곳이나 적혀 있는데도 kanadic 2026-08-19
세션에서 **154회** 반복됐다. 문서로 막히지 않는 것이 증명됐으므로 구조로 강제한다
(CLAUDE.md: "로그에 적은 교훈은 저절로 다음 코드에 옮겨지지 않는다").

**무엇이 문제인가**: 승인 규칙은 명령을 조각으로 나눠 접두사로 검사한다. 맨 앞에 `cd`가 붙으면
어떤 규칙에도 안 걸려 **매번 확인 창이 뜬다.** 작업 디렉토리는 이미 저장소 루트이고 호출
사이에 유지되므로 이 `cd`는 하는 일이 없다.

**막는 변형** (vanasso.kr 2026-09-06 실측 — 10세션 1,000건 중 168건이 아래 형태로 통과했다):
  `cd <루트> 2>/dev/null; cmd`(49) · `cd <루트> cmd`(구분자 없음, 99 — bash는 "too many arguments"로
  죽고 cmd는 돌지도 않는다) · `cd /c/Users/…`(Git Bash MSYS 경로, 20).

**막지 않는 것**:
  - `cd app` / `cd ..` 처럼 **디렉토리만 바꾸는 단독 호출** (하위 디렉토리 작업에 필요하다)
  - 저장소 루트가 아닌 곳으로 가는 `cd` (루트 아래 하위 디렉토리 포함)

**판정 패턴은 `scripts/measure_approvals.py`에서 import한다** — 같은 규칙이 두 곳에 있으면
조용히 어긋난다(CLAUDE.md '같은 규칙이 두 곳 이상' 조항). 그쪽 패턴은 저장소 경로를
하드코딩하지 않고 파일 위치에서 계산하므로 프로젝트 간 복사가 그대로 된다.
import에 실패하면 **막지 않고 통과**시킨다 — 훅 결함이 도구 사용을 봉쇄하면 안 된다.
"""

import sys
from pathlib import Path

from hook_io import deny, read_command

ROOT = Path(__file__).resolve().parents[2]

REASON = (
    "작업 디렉토리는 이미 저장소 루트이고 호출 사이에 유지된다 — 이 `cd`는 하는 일이 없다.\n"
    "  그런데 맨 앞에 `cd`가 붙으면 **어떤 허용 규칙에도 안 걸려 매번 승인을 묻는다.**\n"
    "  → `cd ...&&` 를 떼고 명령만 그대로 부를 것.\n"
    "  (하위 디렉토리로 가야 하면 `cd <dir>`를 **단독 호출**로 하고 끝나면 `cd ..`로 돌아온다.)"
)


def main() -> int:
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from measure_approvals import REDUNDANT_CD_COMMAND
    except Exception as e:
        # 패턴 소스를 못 읽었다고 도구를 막지 않는다. 조용히 통과시키되 이유를 남긴다.
        print(f"no_redundant_cd: 패턴 import 실패로 통과시킨다 ({e})", file=sys.stderr)
        return 0

    command = read_command("no_redundant_cd")
    if command is None:
        return 0
    if REDUNDANT_CD_COMMAND.match(command):
        deny(REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
