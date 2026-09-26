r"""셸로 파일을 쓰는 호출(`cat > / cat >>` + heredoc, `sed -i`)을 막는다. 파일은 `Write`/`Edit` 도구로 고친다.

**왜 훅인가**: bid-collectors 2026-09-26 Phase 010 `/approval-audit` 실측 — 이 세션 승인 대기의 **최대 원인**이었다.
테스트 파일에 `cat >> tests/test_x.py <<'EOF' … EOF`로 덧붙인 호출이 **4회 모두** 확인 창을 만들었고(허용 규칙이 없는 셸
쓰기), 사용자가 자리를 비운 동안 몇 시간씩 멈췄다. `sed -i`(1회)도 같다. 같은 일을 `Edit`/`Write` 도구로 하면
`acceptEdits` 모드라 묻지 않고, 편집 내역이 도구 기록에 남는다(셸 쓰기는 "파일이 디스크에서 바뀌었다" 경고만 남긴다).

이 규칙은 문서가 아니라 훅으로 둔다 — `cd` 함정이 문서로는 154회 반복된 것과 같은 유형의 **습관**이다.

**막는 것**: `cat`의 출력을 파일로 보내는 리다이렉트(`>`·`>>`, heredoc 여부 무관) · `sed -i`/`--in-place`.
**막지 않는 것** (반례 — 테스트에 들어 있다):
  - `cat file.txt`, `cat a b | wc -l` — 읽기
  - `> /dev/null`·`2>` 버리기 리다이렉트, `python scripts/x.py > out.txt`(cat이 아닌 명령의 출력 저장 — 재본 적이 없다)
  - `sed -n 1p file`, `grep -i`, `git commit -F .commit_msg.txt`
  - `concat`처럼 cat으로 끝나는 다른 낱말

**훅이 고장 나면 막지 않고 통과시킨다** — 훅 결함이 도구 사용을 봉쇄하면 안 된다(다른 훅과 같은 원칙).
"""

import re
import sys

from hook_io import deny, read_command

# 첫 줄(heredoc 본문 제외)에서: 명령 머리의 `cat` … `>`/`>>` 파일 — `2>`·`&>`와 `/dev/null`은 제외
CAT_WRITE = re.compile(r"(?:^|[\s;&|(])cat\b[^\n|;&]*?(?<![0-9&])>{1,2}\s*(?!/dev/null\b)[^\s&|;>]")
SED_INPLACE = re.compile(r"(?:^|[\s;&|(])sed\s+(?:-[^\s]*\s+)*?(?:-[A-Za-z]*i|--in-place)")

REASON = (
    "셸로 파일을 쓰지 말 것(`cat > / cat >>` + heredoc, `sed -i`) — `Write`/`Edit` 도구를 쓴다.\n"
    "  셸 쓰기는 허용 규칙이 없어 **매번 승인을 묻는다**(bid-collectors 2026-09-26 실측: 테스트 파일 덧붙이기 4회가\n"
    "  이 세션 최대 대기 원인). `Edit`/`Write`는 acceptEdits 모드라 묻지 않고 편집 내역도 남는다.\n"
    "  → 파일 끝에 덧붙이려면 `Edit`로 마지막 부분을 old_string으로 잡아 뒤에 붙인다. 여러 줄 치환도 `Edit`."
)


def blocked(command: str) -> bool:
    """이 명령을 막아야 하는가. 테스트가 이 함수를 직접 부른다."""
    first_line = (command or "").split("\n", 1)[0]
    return bool(CAT_WRITE.search(first_line) or SED_INPLACE.search(first_line))


def main() -> int:
    command = read_command("no_shell_file_write")
    if command is None:
        return 0
    if blocked(command):
        deny(REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
