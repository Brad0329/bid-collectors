r"""훅 4개의 **deny 출력이 콘솔 인코딩과 무관하게 나가는지** 검증한다.

**이 테스트의 존재 이유** (2026-08-22 실측): 훅 4개가 전부 죽어 있었는데 기존 테스트 114건은
전부 초록불이었다. `json.dumps(..., ensure_ascii=False)`가 cp949 콘솔에서 `—`(U+2014)를
못 찍어 `UnicodeEncodeError`로 exit 1 → 하네스는 막지 않고 통과. **훅 4개가 조용히 무력화.**

기존 테스트가 못 잡은 이유는 **환경변수 때문**이다 — pytest를 부르는 PowerShell 도구 환경에
`PYTHONIOENCODING=utf-8:surrogateescape`가 들어 있어 자식 파이썬이 UTF-8로 찍었다.
하네스가 훅을 띄울 때는 그 변수가 없다. 초록불이 실력이 아니라 환경 덕이었다.

→ 그래서 여기서는 **`PYTHONIOENCODING`을 cp949로 고정**해 그 조건을 재현한다.
   이 변수를 지우면(=현재 환경 그대로 물려받으면) 이 테스트는 다시 아무것도 검증하지 않는다.

실행: python -m pytest tools_tests/test_hook_io.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"

# (훅 파일, 막혀야 하는 명령, 통과해야 하는 명령)
CASES = [
    ("no_redundant_cd.py", f"cd {ROOT.as_posix()} && git log --oneline -1", "cd app"),
    ("no_output_filter.py", "flutter test 2>&1 | Select-Object -Last 12", "flutter test"),
    ("no_inline_python.py", 'python -c "print(1)"', "python scripts/measure_wait.py"),
    ("no_scratchpad_path.py",
     "python C:/Temp/claude/d0fe3eab-29eb-4a31-a94d-b3db864de789/scratchpad/x.py",
     "python scripts/measure_wait.py"),
]

# 선택형 훅 — 초기화 때 Flutter가 아니면 파일째 지운다(CLAUDE.md 초기화 5번). 지웠으면 여기서도
# 자동으로 빠진다. 위 3종은 스택 중립이라 항상 있어야 하고, 없으면 그대로 실패한다(조용히 빼지 않는다).
# 2026-09-05 초기화 시뮬레이션에서 이 훅을 지우자 이 파일의 4건이 "파일 없음"으로 죽은 실사례.
_OPTIONAL = [
    ("no_targeted_flutter_test.py", "flutter test test/x_test.dart", "flutter test"),
]
CASES += [c for c in _OPTIONAL if (HOOKS / c[0]).exists()]

HOOK_IDS = [c[0] for c in CASES]


def _run(hook: str, command: str) -> subprocess.CompletedProcess:
    """훅을 **cp949 콘솔인 척**하는 환경에서 돌린다 — 하네스가 훅을 띄우는 조건의 재현이다."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


@pytest.mark.parametrize("hook,blocked_command,_ok", CASES, ids=HOOK_IDS)
def test_cp949_콘솔에서도_deny가_나간다(hook, blocked_command, _ok):
    p = _run(hook, blocked_command)
    assert p.returncode == 0, f"훅이 죽었다 — 하네스는 그냥 통과시킨다:\n{p.stderr}"
    assert p.stdout.strip(), f"deny를 못 냈다 (stderr: {p.stderr})"
    decision = json.loads(p.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


@pytest.mark.parametrize("hook,blocked_command,_ok", CASES, ids=HOOK_IDS)
def test_deny_출력은_순수_ASCII다(hook, blocked_command, _ok):
    """어떤 인코딩으로 디코드하든 안전해야 한다 — `ensure_ascii`를 끄면 여기서 걸린다."""
    out = _run(hook, blocked_command).stdout
    assert out.isascii(), "비ASCII가 그대로 나간다 — 콘솔 인코딩에 따라 훅이 죽는다"
    # 이스케이프를 풀면 한글 안내가 그대로 있어야 한다 (사용자가 읽을 내용은 안 잃는다).
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert not reason.isascii(), "안내 문구가 통째로 사라졌다"


@pytest.mark.parametrize("hook,_blocked,ok_command", CASES, ids=HOOK_IDS)
def test_cp949_콘솔에서도_정상_호출은_통과한다(hook, _blocked, ok_command):
    p = _run(hook, ok_command)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "", f"막으면 안 되는 호출을 막았다: {ok_command}"


@pytest.mark.parametrize("hook", HOOK_IDS)
def test_cp949_콘솔에서_입력이_깨져도_훅이_안_죽는다(hook):
    """진단 메시지(한글)를 찍다가 죽으면 그것도 같은 사고다."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"
    env.pop("PYTHONUTF8", None)
    p = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input="{깨진 json",
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert p.returncode == 0, f"진단 출력에서 죽었다:\n{p.stderr}"
    assert p.stdout.strip() == ""      # deny를 내지 않는다
    assert p.stderr.strip()            # 조용히 넘기지 않는다 (silent failure 금지)
