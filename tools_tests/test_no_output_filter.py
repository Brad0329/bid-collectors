"""출력 필터 훅(`.claude/hooks/no_output_filter.py`) 검증.

**반례가 이 테스트의 존재 이유다.** 훅은 너무 많이 막으면 도구를 못 쓰게 만든다 —
`no_redundant_cd`가 `cd app` 단독 호출을 막으면 안 되는 것과 같은 자리다.
여기서는 **자르기가 아닌 파이프**(`| Out-File`·`| ConvertFrom-Json`)를 막으면 안 된다.

실행: python -m pytest tools_tests/test_no_output_filter.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_output_filter.py"
sys.path.insert(0, str(HOOK.parent))
from no_output_filter import blocked  # noqa: E402


# ── 막아야 하는 것 — 2026-08-20 실측에서 실제로 비용을 만든 형태들 ──────────────

@pytest.mark.parametrize("command", [
    "flutter test 2>&1 | Select-Object -Last 12",          # 209.0초 (맨몸 27.4초)
    "flutter test test/x_test.dart 2>&1 | Select-String -Pattern 'a|b'",  # 89.0초
    "flutter test | Select-Object -Last 5",                 # 파이프만 붙어도 새 문자열이다
    "git status --short 2>&1",                             # 2>&1만 붙어도
    "flutter analyze | select-string error",               # PowerShell은 대소문자를 안 가린다
    ".venv\\Scripts\\python.exe -m pytest -q 2>&1 | Select-Object -Last 6",
])
def test_출력_필터를_막는다(command):
    assert blocked(command)


# ── 막으면 안 되는 것 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "flutter test",                                        # 맨몸 — 권장하는 형태다
    "flutter test test/x_test.dart",
    "git log --oneline -1 | Out-File out.txt",             # 자르기가 아니라 쓰기다
    "Get-Content x.json | ConvertFrom-Json",               # 자르기가 아니라 변환이다
    "git status --short",
    "Set-Location C:\\Users\\user\\Documents\\hanjadic",
    # `Select`가 다른 낱말의 일부일 때 — 단어 경계가 있어야 한다
    "python scripts/select_objects.py",
    "echo 'Select-Object'",                                # 파이프 뒤가 아니면 명령이 아니다
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
    out = _run({"tool_input": {"command": "flutter test 2>&1 | Select-Object -Last 12"}})
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "맨몸" in decision["permissionDecisionReason"]


def test_훅이_정상_호출에는_아무_말도_안_한다():
    assert _run({"tool_input": {"command": "flutter test"}}).strip() == ""


def test_입력이_깨져도_막지_않는다():
    """훅 결함이 도구 사용을 봉쇄하면 안 된다."""
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input="{깨진 json",
        capture_output=True,
        text=True,
        encoding="utf-8",
        # 자식의 stderr 인코딩을 고정한다. 물려받으면 PowerShell 도구(utf-8 설정됨)에서는
        # 통과하고 Bash 도구(미설정 → cp949)에서는 디코드 실패로 stderr가 None이 된다
        # (2026-08-22 실측). 인코딩 자체의 회귀는 test_hook_io.py가 cp949 고정으로 본다.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""          # deny를 내지 않는다
    assert "통과" in p.stderr              # 조용히 넘기지 않는다 (silent failure 금지)
