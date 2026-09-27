"""위치 접두사 훅(`.claude/hooks/no_redundant_cd.py`) 검증 — 루트 cd·다른 디렉토리 cd·`git -C <루트>`.

루트 cd·git -C 패턴 자체의 경계값은 `test_measure_approvals.py`가 잰다(패턴 소스가 그쪽이다).
여기서는 **훅이 무엇을 막고 무엇을 통과시키는지**를 본다 — 특히 반례: 단독 `cd`와 다른 저장소(bidwatch)의
`git -C`는 정당한 호출이라 막으면 일이 멈춘다. 끝의 파이프 테스트는 실제 stdin→stdout 경로를 한 번 탄다
(통과하는 쪽만 보면 죽은 훅과 정상 훅의 증상이 같다 — approval-audit 스킬).

★ 저장소 경로는 하드코딩하지 않는다 — 이 파일은 템플릿에서 가져왔다(greenfield 041b2b5).
실행: python -m pytest tools_tests/test_no_redundant_cd.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_redundant_cd.py"
sys.path.insert(0, str(HOOK.parent))
from no_redundant_cd import REASON_GIT_C, REASON_OTHER, REASON_ROOT, blocked  # noqa: E402

REPO = ROOT.as_posix()
REPO_MSYS = "/" + REPO[0].lower() + REPO[2:]


@pytest.mark.parametrize("command", [
    f"cd {REPO} && git status",
    f"cd {REPO_MSYS} 2>/dev/null; ls",
])
def test_루트_cd는_루트_사유로_막는다(command):
    assert blocked(command) == REASON_ROOT


@pytest.mark.parametrize("command", [
    # bid-collectors 2026-09-26 서브에이전트 실측 형태 — 건당 최대 422초
    f"cd {REPO_MSYS}/scripts/_tmp/d2b_url && ../../../.venv/Scripts/python.exe collect.py",
    "cd scripts && ls",
    "cd .. ; ls",
    "cd /c/Users/user/Documents/bidwatch && ls backend",
    'cd "scripts/_tmp" && for f in a b; do echo $f; done',
])
def test_다른_디렉토리로_옮긴_뒤_명령도_막는다(command):
    assert blocked(command) == REASON_OTHER


@pytest.mark.parametrize("command", [
    f"git -C {REPO} diff --stat",
    f"git -C {REPO_MSYS} log --oneline -6",
    f'git -C "{ROOT}" status --short',                  # 역슬래시 원형 + 따옴표
    f"git -C {REPO}/ status",
    f"git -C {REPO_MSYS} add docs/x.md",                # 쓰는 git (2026-09-26 311초)
    f"ls; git -C {REPO} diff",                          # 앞에 무엇이 붙어도
])
def test_루트를_가리키는_git_C를_막는다(command):
    assert blocked(command) == REASON_GIT_C


@pytest.mark.parametrize("command", [
    "cd app",                                           # 단독 cd — 정말 옮겨 가야 할 때의 길
    "cd ..",
    f"cd {REPO}",
    "git -C C:/Users/user/Documents/bidwatch log --oneline -1",   # 다른 저장소 — 정당하다
    "git -C /c/Users/user/Documents/bidwatch fetch origin",
    f"git -C {REPO}-other status",                      # 이름이 루트로 시작하는 다른 디렉토리
    f"git -C {REPO}/sub status",                        # 루트가 아닌 하위 — 재본 적 없다(YAGNI)
    "git diff --stat",
    "python scripts/measure_wait.py",
    "git commit -F - <<'EOF'\ncd x && y 는 막는다\nEOF",  # heredoc 본문의 글 — 명령이 아니다
    "",
])
def test_정상_호출은_막지_않는다(command):
    assert blocked(command) is None


# ── 실제 파이프 한 번 ─────────────────────────────────────────────────────────

def _run(command: str) -> str:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_파이프로_막는_호출은_deny를_낸다():
    for command in (f"cd {REPO} && git status", "cd scripts && ls", f"git -C {REPO} status"):
        decision = json.loads(_run(command))["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny", command


def test_파이프로_정상_호출에는_아무_말도_안_한다():
    for command in ("git add docs/x.md", "cd app", "git -C C:/Users/user/Documents/bidwatch status",
                    "python -m pytest tests/"):
        assert _run(command).strip() == "", command
