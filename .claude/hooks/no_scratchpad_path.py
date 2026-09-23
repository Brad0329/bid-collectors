r"""셸 명령에 **세션 UUID가 든 스크래치패드 절대경로**가 있으면 막는다 (PreToolUse 훅).

**왜 훅인가**: CLAUDE.md '작업 속도 규칙'에 금지로 적혀 있는데도 hanmunstudy 2026-08-22 세션에서
다시 밟았다(`노하우_승인_대기_최소화.md` [H11] — 스크래치패드 절대경로 스크립트 3건이
21.5·11.8·10.2초 승인 대기, "CLAUDE.md가 금지한 두 항목을 같은 세션에서 다시 밟음").
문서로 막히지 않는 것이 확인됐으므로 구조로 강제한다.

**무엇이 문제인가**: 경로에 세션 UUID가 박혀 있어 어떤 허용 규칙에도 안 걸린다 → 매번 묻는다.
사용자가 "항상 허용"을 눌러도 **다음 세션에는 UUID가 달라 두 번 다시 안 걸리는 죽은 규칙**만 남는다.

**막지 않는 것** (반례 — 테스트에 들어 있다):
  - `Read`/`Write`/`Edit` 도구로 스크래치패드 파일을 다루는 것 (이 훅은 셸 명령만 본다)
  - UUID가 없는 임시 경로, UUID만 있고 스크래치패드가 아닌 경로
  - **한글 인자** — 같은 문장에 묶여 있던 규칙이지만 훅으로 옮기지 않았다. 와일드카드 규칙이 있으면
    한글 경로도 안 묻고([H12] 실측: `git add docs/playbooks/노하우_….md`), 이 템플릿은 문서명이
    전부 한글이라 막으면 정상 호출이 막힌다. 문서 규칙으로 남긴다.

**훅이 고장 나면 막지 않고 통과시킨다** — 훅 결함이 도구 사용을 봉쇄하면 안 된다.
"""

import re
import sys

from hook_io import deny, read_command

# `<세션 UUID>/scratchpad` — 구분자는 `\`·`/` 어느 쪽이든, 겹쳐 써도(`\\`) 잡는다.
SCRATCHPAD_PATH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[\\/]+scratchpad",
    re.IGNORECASE,
)

REASON = (
    "셸 명령에 스크래치패드 절대경로를 쓰지 말 것 — 세션 UUID가 박혀 어떤 허용 규칙에도 안 걸린다.\n"
    "  매번 승인을 묻고(실측 10~21초), '항상 허용'을 눌러도 다음 세션엔 안 걸리는 죽은 규칙만 남는다.\n"
    "  → 스크래치패드 **파일**은 `Read`/`Write`/`Edit` 도구로 다룬다(셸을 거치지 않는다).\n"
    "  → 돌릴 **스크립트**는 `scripts/`에 두고 상대경로로 부른다(일회성이면 gitignore된 `scripts/_tmp/`에\n"
    "     덮어써서 쓰고 지우지 않는다 — 삭제 명령은 매번 승인을 묻는다).\n"
    "     스크립트가 임시 경로를 써야 하면 그 경로는 인자로 넘기지 말고 스크립트 안에서 계산한다."
)


def blocked(command: str) -> bool:
    """이 명령을 막아야 하는가. 테스트가 이 함수를 직접 부른다."""
    return bool(SCRATCHPAD_PATH.search(command or ""))


def main() -> int:
    command = read_command("no_scratchpad_path")
    if command is None:
        return 0
    if blocked(command):
        deny(REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
