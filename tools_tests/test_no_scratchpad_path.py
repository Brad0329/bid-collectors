"""스크래치패드 경로 훅(`.claude/hooks/no_scratchpad_path.py`) 검증.

**반례가 이 테스트의 존재 이유다.** 이 훅이 한글 경로나 평범한 임시 경로를 막으면
`git add docs/playbooks/노하우_….md` 같은 정상 호출이 막힌다(한글 인자를 훅으로 옮기지 않은 이유).

실행: python -m pytest tools_tests/test_no_scratchpad_path.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_scratchpad_path.py"
sys.path.insert(0, str(HOOK.parent))
from no_scratchpad_path import blocked  # noqa: E402

UUID = "d0fe3eab-29eb-4a31-a94d-b3db864de789"


# ── 막아야 하는 것 — [H11]에서 실제로 10~21초 승인 대기를 만든 형태 ─────────────

@pytest.mark.parametrize("command", [
    f"python C:\\Users\\user\\AppData\\Local\\Temp\\claude\\proj\\{UUID}\\scratchpad\\strip.py",
    f"python C:/Users/user/AppData/Local/Temp/claude/proj/{UUID}/scratchpad/strip.py",
    f"python /c/Users/user/AppData/Local/Temp/claude/proj/{UUID}/scratchpad/strip.py",   # Git Bash
    f'python "C:\\\\Temp\\\\claude\\\\{UUID}\\\\scratchpad\\\\x.py"',                     # 겹친 역슬래시
    f"python scripts/probe.py --out C:/Temp/claude/{UUID}/scratchpad/out.json",          # 인자로만 와도
    f"Get-Content C:\\Temp\\claude\\{UUID.upper()}\\Scratchpad\\a.txt",                  # 대소문자
])
def test_스크래치패드_절대경로를_막는다(command):
    assert blocked(command)


# ── 막으면 안 되는 것 (반례) ─────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "python scripts/measure_wait.py",
    "git add docs/playbooks/노하우_검증.md",              # 한글 인자는 이 훅의 대상이 아니다
    "git commit -F .commit_msg.txt",
    "python scripts/probe.py --out C:/Temp/out.json",     # UUID 없는 임시 경로
    f"git show {UUID}",                                   # UUID만 있고 스크래치패드가 아니다
    "python scripts/scratchpad_cleanup.py",               # 낱말만 같다
    "",
])
def test_정상_호출은_막지_않는다(command):
    assert not blocked(command)


# ── 훅을 실제로 파이프로 돌려 본다 ───────────────────────────────────────────

def _run(command: str) -> str:
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_훅이_deny를_돌려준다():
    out = _run(f"python C:/Temp/claude/{UUID}/scratchpad/x.py")
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "scripts/" in decision["permissionDecisionReason"]


def test_훅이_정상_호출에는_아무_말도_안_한다():
    assert _run("git add docs/playbooks/노하우_검증.md").strip() == ""
