"""측정기 2종(`measure_wait.py`·`measure_approvals.py`)이 **cp949 콘솔에서 죽지 않는지** 검증한다.

**존재 이유** (vanasso.kr 2026-09-06 실측): `measure_wait.py`가 Bash 도구 환경에서 `[규모]` 줄까지
찍고 `—`(U+2014)에서 `UnicodeEncodeError`로 죽었다. `hook_io.py`가 고친 사고와 같은 뿌리인데
측정기는 `measure_approvals`만 `reconfigure`가 있었고 `measure_wait`에는 없었다 — 한쪽만 고쳐진 것.
PowerShell 도구 환경에는 `PYTHONIOENCODING=utf-8`이 있어 재현되지 않는다(`[H13]` "초록불이 환경변수 덕").
그래서 여기서는 훅 테스트처럼 **cp949로 고정**해 돌린다.

트랜스크립트는 임시 디렉토리에 합성해 `CLAUDE_TRANSCRIPT_DIR`로 가리킨다 — 실제 세션에 의존하지 않는다.
느린 호출(>8초)을 하나 넣어 `—`가 든 "느린 것 합계" 줄이 반드시 찍히게 한다.

실행: python -m pytest tools_tests/test_measure_scripts_encoding.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _session(tmp_path: Path) -> Path:
    d = tmp_path / "transcripts"
    d.mkdir()
    events = [
        {"timestamp": "2026-09-06T10:00:00.000Z",
         "message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                  "input": {"command": "git status"}}]}},
        {"timestamp": "2026-09-06T10:00:30.000Z",          # 30초 — "느린 것" 분기를 태운다
         "message": {"content": [{"type": "tool_result", "tool_use_id": "a"}]}},
    ]
    (d / "s.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return d


def _run(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"      # 하네스·Bash 도구의 콘솔 조건 재현
    env.pop("PYTHONUTF8", None)
    env["CLAUDE_TRANSCRIPT_DIR"] = str(_session(tmp_path))
    return subprocess.run([sys.executable, str(SCRIPTS / script)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


@pytest.mark.parametrize("script,ok_codes", [
    ("measure_wait.py", {0}),
    ("measure_approvals.py", {0, 2}),      # 2 = 표본 부족(정상 종료 경로) — 그 메시지에도 `—`가 있다
])
def test_cp949_콘솔에서_죽지_않는다(script, ok_codes, tmp_path):
    p = _run(script, tmp_path)
    assert "UnicodeEncodeError" not in p.stderr, f"{script}가 cp949에서 죽었다:\n{p.stderr}"
    assert p.returncode in ok_codes, f"{script} exit {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
    assert p.stdout.strip(), "아무것도 안 찍혔다"
