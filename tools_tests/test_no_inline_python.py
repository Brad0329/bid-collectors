"""인라인 파이썬 훅(`.claude/hooks/no_inline_python.py`) 검증.

**반례가 이 테스트의 존재 이유다.** 이 훅이 `python scripts/foo.py`나 `python -m pytest`를
막으면 프로젝트의 테스트·측정기를 통째로 못 돌린다 — 고칠 방법이 없어지는 자리다.

실행: python -m pytest tools_tests/test_no_inline_python.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_inline_python.py"
sys.path.insert(0, str(HOOK.parent))
from no_inline_python import blocked  # noqa: E402


# ── 막아야 하는 것 — 2026-08-22 Phase 001에서 실제로 확인 창을 만든 형태들 ──────

@pytest.mark.parametrize("command", [
    # 실측 9.1초. '항상 허용'을 누르자 이스케이프째 규칙에 박제됐다(재사용 0).
    "python -c \"p=r'C:/tmp/x.html'; s=open(p).read(); print(len(s))\"",
    # 실측 7.7초.
    "python -c \"import os;p='.env';print(os.path.exists(p))\"",
    "python -c 'print(1)'",
    "python3 -c \"print(1)\"",
    "py -c \"print(1)\"",
    "python.exe -c \"print(1)\"",
    "python -u -c \"print(1)\"",          # 다른 플래그가 끼어도
    "PYTHON -C \"print(1)\"",             # PowerShell은 대소문자를 안 가린다
    "git log --oneline -1; python -c \"print(1)\"",   # 앞에 뭐가 붙어도
    # bid-collectors 2026-09-26 실측 150초 — 경로로 부른 인터프리터(앞이 `/`)가 통과하던 구멍
    ".venv/Scripts/python.exe -c \"import re,sys;print(1)\"",
    "C:/Users/user/Documents/bidwatch/backend/.venv/Scripts/python.exe -c \"import bid_collectors\"",
    "C:\\venv\\Scripts\\python.exe -c \"print(1)\"",
])
def test_인라인_파이썬을_막는다(command):
    assert blocked(command)


# ── 막으면 안 되는 것 (반례) ─────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "python scripts/probe_seodang_api.py",           # 스크립트 파일 호출 — 권장하는 형태다
    "python scripts/measure_approvals.py --sessions 3",
    "python -m pytest tests/",                        # 모듈 실행
    "python -m pytest tests/ -q",
    "python --version",
    "python",                                         # 맨몸
    "python scripts/foo.py -c config.json",           # `-c`가 **스크립트의 인자**일 때
    "grep -c pattern file.txt",                       # 다른 명령의 -c
    "sort -c list.txt",
    "flutter test",
    "echo 'python -c'",                               # 낱말로만 등장 — 실행이 아니다
    ".venv/Scripts/python.exe scripts/_tmp/probe.py -c x",   # 경로 인터프리터 + 스크립트의 인자 -c
    ".venv/Scripts/python.exe -m pytest tests -q",
    "cat scripts/mypy.cfg",                           # 경로 안의 py로 끝나는 낱말
])
def test_정상_호출은_막지_않는다(command):
    assert not blocked(command)


def test_빈_명령은_막지_않는다():
    assert not blocked("")


# ── 훅을 실제로 파이프로 돌려 본다 (스킬 요구사항) ───────────────────────────

def _run(payload: dict) -> str:
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_훅이_deny를_돌려준다():
    out = _run({"tool_input": {"command": "python -c \"print(1)\""}})
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "스크립트 파일" in decision["permissionDecisionReason"]


def test_훅이_정상_호출에는_아무_말도_안_한다():
    assert _run({"tool_input": {"command": "python -m pytest tests/"}}).strip() == ""


def test_입력이_깨져도_막지_않는다():
    """훅 결함이 도구 사용을 봉쇄하면 안 된다."""
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input="{깨진 json",
        capture_output=True,
        text=True,
        encoding="utf-8",
        # 자식의 stderr 인코딩을 고정한다 — 이유는 test_no_output_filter.py 같은 자리 참조.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""
