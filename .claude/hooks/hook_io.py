r"""훅들이 공유하는 stdin 읽기 / deny·컨텍스트 주입 출력.

**왜 한 곳으로 모았나 — 훅 4개가 전부 같은 원인으로 죽어 있었다** (2026-08-22 실측):

`json.dumps(..., ensure_ascii=False)`가 **Windows 기본 콘솔 인코딩(cp949)에서
`—`(U+2014)·한글을 못 찍어** `UnicodeEncodeError`로 죽었다. 훅이 죽으면 하네스는 막지 않고
통과시키므로 **4개 훅이 전부 조용히 무력화된 상태**였다 (`cd <루트> && …`도, `2>&1`도,
`python -c`도, `flutter test <파일>`도 전부 그냥 통과했다).

**테스트는 왜 못 잡았나** — pytest를 부르는 PowerShell 도구 환경에는
`PYTHONIOENCODING=utf-8:surrogateescape`가 들어 있어 자식 파이썬도 UTF-8로 찍었다.
하네스가 훅을 띄울 때는 그 변수가 없다. **초록불이 환경변수 덕이었다.**
→ 회귀 테스트는 `PYTHONIOENCODING`을 **cp949로 고정해서** 그 조건을 재현한다
(`tools_tests/test_hook_io.py`).

**고친 방법**: `ensure_ascii`를 기본값(True)으로 되돌린다. 비ASCII가 `\uXXXX`로 이스케이프돼
**콘솔 인코딩과 무관하게 항상 ASCII로 나간다.** 하네스는 JSON을 파싱하므로 한글은 그대로 보인다.
stdout 인코딩을 바꾸는 방식(reconfigure)과 달리 **하네스가 무엇으로 디코드하든 안전하다.**

같은 코드가 4벌 복사돼 있었기에 한 곳을 고쳐도 셋이 남는 구조였다 — 그래서 모듈로 뽑았다
(CLAUDE.md '같은 규칙이 두 곳 이상에 구현되면').
"""

from __future__ import annotations

import json
import sys

# 진단 메시지(한글)를 찍다가 훅이 죽으면 안 된다 — 깨져도 통과시킨다.
# 여기서 실패를 로그로 남기려면 바로 그 stderr가 필요하므로 순환이다. 그래서 조용히 넘긴다.
try:
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def read_input(hook_name: str) -> dict | None:
    """훅 입력 JSON 전체. 못 읽으면 None (= 막지 말고 통과).

    ★ stdin은 **바이트로 읽어 UTF-8로 디코드한다** — `json.load(sys.stdin)`을 쓰지 말 것.
    하네스는 한글을 이스케이프하지 않은 UTF-8 바이트로 보내는데, Windows 파이썬은 파이프 stdin을
    콘솔 인코딩(cp949)으로 읽는다. 그러면 한글이 든 명령은 바이트 조합에 따라 디코드가 **깨지거나 운 좋게
    통과**한다 — 깨지면 None이라 **막아야 할 명령을 조용히 통과**시킨다(2026-09-28 템플릿 691da5c 실측).
    위 docstring의 cp949 사고는 출력 쪽, 이것은 입력 쪽의 같은 뿌리다.
    UTF-8이 아닌 바이트가 오면 replace로 넘긴다 — 판정에 쓰는 ASCII 부분(cat·cd·경로 구분자)은 보존된다.
    """
    try:
        raw = sys.stdin.buffer.read()
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        # 훅이 입력을 못 읽었다고 도구를 막지 않는다. 조용히 통과시키되 이유를 남긴다.
        print(f"{hook_name}: 훅 입력을 읽지 못해 통과시킨다 ({e})", file=sys.stderr)
        return None


def read_command(hook_name: str) -> str | None:
    """훅 입력에서 명령 문자열을 꺼낸다. 못 읽으면 None (= 막지 말고 통과)."""
    data = read_input(hook_name)
    if data is None:
        return None
    return (data.get("tool_input") or {}).get("command", "")


def add_context(event_name: str, text: str) -> None:
    """차단 없이 컨텍스트만 주입한다 (SessionStart 등). `deny`와 같은 이유로 ASCII로만 나간다."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def deny(reason: str) -> None:
    """PreToolUse deny 결정을 stdout으로 낸다.

    `ensure_ascii`를 **끄지 말 것** — 위 docstring의 사고가 그대로 재발한다.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
