r"""`python -c "..."` 인라인 실행을 막는다. 스크립트 파일로 만들어 부를 것.

**왜 훅인가**: 2026-08-22 hanmunstudy Phase 001 `/approval-audit` 실측.

이 세션에서 인라인 파이썬을 두 번 불렀고, 두 번 다 확인 창을 만들었다(9.1초·7.7초).
건수 자체는 작지만 **남는 피해가 크다** — 사용자가 "항상 허용"을 누르면 매칭 코드가
그 호출 문자열을 **이스케이프까지 통째로** `settings.local.json`에 박제한다:

    PowerShell(python -c "p=r'C:/.../scratchpad/seodang_root.html'; s=open\(p,...\).read\(\); ...")

이런 규칙은 **두 번 다시 걸리지 않는다.** 재사용 0인데 목록만 길어지고, 나중에
"규칙이 있는데 왜 묻지"라는 오진을 만든다(템플릿이 경고하는 '죽은 규칙').
같은 세션에서 이런 재사용 불가 규칙이 31건 중 20건까지 쌓였다.

덤으로 **PowerShell 따옴표와 매번 싸운다** — 실제로 중첩 따옴표 때문에
`TerminatorExpectedAtEndOfString` 파서 오류로 한 번 죽었고, 그 자리에서
스크립트 파일(`strip.py`)로 바꾸자 곧바로 해결됐다.

**막지 않는 것** (반례 — 테스트에 들어 있다):
  - `python scripts/foo.py` 처럼 **스크립트 파일**을 부르는 정상 호출
  - `python --version`, `python -m pytest tests/` 같은 모듈·플래그 호출
  - 명령 문자열 안에 `-c`가 **인자로만** 등장하는 경우(`grep -c`, `sort -c` 등)

**훅이 고장 나면 막지 않고 통과시킨다** — 훅 결함이 도구 사용을 봉쇄하면 안 된다
(`no_redundant_cd.py`·`no_output_filter.py`와 같은 원칙).
"""

import re
import sys

from hook_io import deny, read_command

# `python`/`python3`/`py` 뒤에 (다른 플래그가 끼어도) `-c`가 오는 형태.
# 앞은 줄머리 또는 공백·파이프·세미콜론 뒤여야 한다 — 다른 낱말의 꼬리에 걸리지 않게.
INLINE_PYTHON = re.compile(
    r"(?:^|[\s|;&])(?:python3?|py)(?:\.exe)?\s+(?:-[A-Za-z]+\s+)*-c(?:\s|$)",
    re.IGNORECASE,
)

REASON = (
    "`python -c \"...\"` 인라인 실행을 쓰지 말 것 — 스크립트 파일로 만들어 부른다.\n"
    "  ① 사용자가 '항상 허용'을 누르면 그 긴 문자열이 이스케이프째 규칙에 박제되는데\n"
    "     **두 번 다시 걸리지 않는다**(재사용 0, 죽은 규칙만 쌓인다. 실측: 31건 중 20건).\n"
    "  ② PowerShell 중첩 따옴표와 매번 싸운다(실측: 파서 오류로 한 번 죽었다).\n"
    "  → `scripts/`에 `.py` 파일로 쓰고 `python <파일> <인자>`로 부른다(일회성이면 gitignore된\n"
    "     `scripts/_tmp/`에 덮어써서 쓰고 지우지 않는다 — 삭제 명령은 매번 승인을 묻는다.\n"
    "     스크래치패드 절대경로로 부르면 `no_scratchpad_path` 훅이 막는다).\n"
    "     파일을 읽고 싶은 것뿐이라면 `Read`/`Grep` 도구를 쓴다."
)


def blocked(command: str) -> bool:
    """이 명령을 막아야 하는가. 테스트가 이 함수를 직접 부른다."""
    return bool(INLINE_PYTHON.search(command or ""))


def main() -> int:
    command = read_command("no_inline_python")
    if command is None:
        return 0
    if blocked(command):
        deny(REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
