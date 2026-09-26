"""셸 파일 쓰기 훅(`.claude/hooks/no_shell_file_write.py`) 검증.

**반례가 이 테스트의 존재 이유다** — 이 훅이 `cat` 읽기나 `/dev/null` 리다이렉트까지 막으면 일상 호출이 막힌다.
실행: python -m pytest tools_tests/test_no_shell_file_write.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "no_shell_file_write.py"
sys.path.insert(0, str(HOOK.parent))
from no_shell_file_write import blocked  # noqa: E402


# ── 막아야 하는 것 — bid-collectors 2026-09-26 Phase 010에서 실제로 확인 창을 만든 형태들 ──

@pytest.mark.parametrize("command", [
    "cat >> tests/test_kogas.py <<'EOF'\n\n# 본문 > 기호도 있다\nEOF",   # 실측 4회
    "cat > scripts/_tmp/x.py <<EOF\nprint(1)\nEOF",
    "cat <<'EOF' > docs/x.md\n본문\nEOF",
    "cat a.txt b.txt > merged.txt",
    "cat >>tests/x.py <<'EOF'\nEOF",                                  # 붙여 쓴 >>
    "sed -i 's#a#b#' docs/REQUIREMENTS.md && grep -c b docs/REQUIREMENTS.md",   # 실측 1회
    "sed -E -i 's/a/b/' x",
    "sed -i.bak 's/a/b/' x",
    "sed --in-place 's/a/b/' x",
    "git status; cat > x.txt <<EOF\nEOF",                             # 앞에 뭐가 붙어도
])
def test_셸_파일_쓰기를_막는다(command):
    assert blocked(command)


# ── 막으면 안 되는 것 (반례) ─────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "cat docs/x.md",
    "cat a b | wc -l",
    "cat x > /dev/null",
    "cat x 2> err.txt",                    # stderr 리다이렉트는 파일 쓰기가 아니라 로그 — 재본 적 없다
    "cat x 2>/dev/null",
    "python scripts/x.py > out.txt",       # cat이 아닌 명령 — 재본 적이 없다(YAGNI)
    "sed -n 1p file",
    "sed 's/a/b/' x",                      # 표준 출력으로만
    "grep -i foo x",
    "git commit -F .commit_msg.txt",
    "git commit -F - <<'EOF'\nfix: 셸 cat > file 형태를 훅으로 막음\nEOF",   # heredoc 본문의 글 — 명령 줄이 아니다
    "echo concat > /dev/null",
    "",
])
def test_정상_호출은_막지_않는다(command):
    assert not blocked(command)


# ── 훅을 실제로 파이프로 돌려 본다 ───────────────────────────────────────────

def _run(payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_훅이_deny를_돌려준다():
    p = _run({"tool_input": {"command": "cat >> tests/x.py <<'EOF'\nx\nEOF"}})
    assert p.returncode == 0, p.stderr
    decision = json.loads(p.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Edit" in decision["permissionDecisionReason"]


def test_훅이_정상_호출에는_아무_말도_안_한다():
    p = _run({"tool_input": {"command": "cat docs/x.md"}})
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_입력이_깨져도_막지_않는다():
    p = _run("{깨진 json")
    assert p.returncode == 0 and p.stdout.strip() == ""
