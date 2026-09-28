"""승인 창 기록 훅(`.claude/hooks/log_permission_request.py`) 검증 (템플릿 fca5c54).

이 훅의 위험은 **승인 흐름을 건드리는 것**이다 — stdout에 무엇이라도 찍으면 하네스가 결정이나 오류로 읽는다.
그래서 "기록은 남기고, stdout은 비고, exit 0"을 cp949 콘솔 조건에서 잰다(hook_io.py의 사고 재현 조건).
실행: python -m pytest tools_tests/test_log_permission_request.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "log_permission_request.py"
sys.path.insert(0, str(HOOK.parent))
from log_permission_request import record  # noqa: E402

# 공식 문서(code.claude.com/docs/en/hooks 'PermissionRequest input')의 예시 모양 + 서브에이전트 필드
SAMPLE = {
    "session_id": "abc123",
    "cwd": "/somewhere/else",
    "permission_mode": "acceptEdits",
    "hook_event_name": "PermissionRequest",
    "agent_id": "agent-1",
    "agent_type": "researcher",
    "tool_name": "Bash",
    "tool_input": {"command": "git fetch --dry-run", "description": "x"},
    "permission_suggestions": [
        {"type": "addRules", "behavior": "allow", "destination": "localSettings",
         "rules": [{"toolName": "Bash", "ruleContent": "git fetch *"}, {"toolName": "WebFetch"}]},
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"},   # 규칙이 아니다 — 빼야 한다
        {"type": "addRules", "behavior": "deny", "destination": "localSettings",
         "rules": [{"toolName": "Bash", "ruleContent": "rm *"}]},              # 허용 제안이 아니다
    ],
}


def test_기록_한_줄에_필요한_칸이_다_있다():
    rec = record(SAMPLE)
    assert rec["session_id"] == "abc123"
    assert rec["agent_type"] == "researcher" and rec["agent_id"] == "agent-1"
    assert rec["tool_name"] == "Bash" and rec["target"] == "git fetch --dry-run"
    # 제안 규칙은 settings 문법 그대로, 허용 addRules만
    assert rec["suggestions"] == ["Bash(git fetch *)", "WebFetch"]


def test_셸이_아니면_경로나_URL을_대상으로_적는다():
    assert record({"tool_name": "Write", "tool_input": {"file_path": "C:/x/y.txt"}})["target"] == "C:/x/y.txt"
    assert record({"tool_name": "WebFetch", "tool_input": {"url": "https://a.b/c"}})["target"] == "https://a.b/c"


def _run(stdin: str, project: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"        # 하네스가 훅을 띄우는 조건 재현(test_hook_io.py와 같은 이유)
    env.pop("PYTHONUTF8", None)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)


def test_훅은_기록만_하고_승인_흐름을_건드리지_않는다(tmp_path):
    (tmp_path / ".claude").mkdir()
    payload = dict(SAMPLE, tool_input={"command": "python scripts/노하우_검사.py — 한글"})
    p = _run(json.dumps(payload, ensure_ascii=False), tmp_path)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "", "stdout에 무엇이든 찍히면 하네스가 결정/오류로 읽는다"
    lines = (tmp_path / ".claude" / "permission_requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["target"] == "python scripts/노하우_검사.py — 한글"   # cp949 콘솔에서도 원문 보존


def test_두_번_불리면_두_줄로_쌓인다(tmp_path):
    (tmp_path / ".claude").mkdir()
    _run(json.dumps(SAMPLE), tmp_path)
    _run(json.dumps(SAMPLE), tmp_path)
    assert len((tmp_path / ".claude" / "permission_requests.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_입력이_깨져도_승인_창을_막지_않고_이유를_남긴다(tmp_path):
    (tmp_path / ".claude").mkdir()
    p = _run("{깨진 json", tmp_path)
    assert p.returncode == 0 and p.stdout == ""
    assert p.stderr.strip()                                  # 조용히 넘기지 않는다
    assert not (tmp_path / ".claude" / "permission_requests.jsonl").exists()
