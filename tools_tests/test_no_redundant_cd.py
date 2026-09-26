"""`.claude/hooks/no_redundant_cd.py`를 실제 파이프로 돌려 본다 — 판정 패턴 자체는 test_measure_approvals.py가 잰다.

통과하는 쪽만 보면 죽은 훅과 정상 훅의 증상이 같다 — 막히는 것·정상인 것을 둘 다 부른다(approval-audit 스킬).
실행: python -m pytest tools_tests/test_no_redundant_cd.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_redundant_cd.py"
REPO = str(ROOT).replace("\\", "/")
REPO_MSYS = "/" + REPO[0].lower() + REPO[2:]


def _run(command: str) -> str:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return p.stdout


def _reason(command: str) -> str:
    decision = json.loads(_run(command))["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


def test_루트_cd를_막는다():
    assert "cd" in _reason(f"cd {REPO} && git status")


def test_루트_git_C를_막는다():
    """bid-collectors 2026-09-26 — `git -C <루트> add`가 `git add *` 규칙을 벗어나 확인 창을 만들었다."""
    assert "git -C" in _reason(f"git -C {REPO_MSYS} add docs/x.md")


def test_정상_호출에는_아무_말도_안_한다():
    for command in ("git add docs/x.md", "cd app", f"git -C {REPO}/scripts status", f"git -C {REPO} status",
                    "python -m pytest tests/"):
        assert _run(command).strip() == "", command
