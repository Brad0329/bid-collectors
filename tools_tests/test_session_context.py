"""세션 시작 주입 훅(`.claude/hooks/session_context.py`) 검증.

이 훅은 CLAUDE.md '새 세션 시작 시' 규칙 2개를 대신한다 — 틀린 Phase를 주입하면 다음 세션이
통째로 엉뚱한 Phase에서 시작하므로, 실제 plan.md 모양의 파일로 돌려 본다.

실행: python -m pytest tools_tests/test_session_context.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "session_context.py"
sys.path.insert(0, str(HOOK.parent))
from session_context import build_context  # noqa: E402

PLAN = """# 전체 계획

## Phase 체크리스트

> 가장 위험한 것을 먼저 한다.

- [x] Phase 001: 요구사항 확정 (2026-09-01)
- [x] Phase 002: 프로젝트 세팅 (2026-09-03)
  - [ ] 하위 항목은 Phase 줄이 아니다
- [ ] Phase 003: MVP 코어
- [ ] Phase 004: 검색
- [ ] 운영 단계 전환

## 사용자 실테스트 대기
- 실기기에서 저장 후 재시작
- <자동화 불가 항목: 플레이스홀더>
- 오프라인에서 검색

## 보류 항목
- 이 줄은 실테스트 대기가 아니다
"""


def _project(tmp_path: Path, plan: str | None = PLAN, claude_md: str = "# 프로젝트\n") -> Path:
    (tmp_path / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (tmp_path / "work_log").mkdir()
    if plan is not None:
        (tmp_path / "work_log" / "plan.md").write_text(plan, encoding="utf-8")
    return tmp_path


def test_첫_미완료_Phase가_현재_Phase다(tmp_path):
    text = build_context(_project(tmp_path))
    assert "현재 Phase: Phase 003: MVP 코어" in text
    assert "Phase 2/4 완료" in text          # 하위 항목·'운영 단계 전환'은 Phase 줄이 아니다


def test_Phase_줄은_자르지_않고_전부_싣는다(tmp_path):
    text = build_context(_project(tmp_path))
    for n in ("001", "002", "003", "004"):
        assert f"Phase {n}:" in text


def test_현재_Phase의_로그가_있으면_읽으라고_한다(tmp_path):
    root = _project(tmp_path)
    (root / "work_log" / "Phase_003.md").write_text("# 로그\n", encoding="utf-8")
    assert "work_log/Phase_003.md도 읽을 것" in build_context(root)


def test_다른_Phase의_로그는_현재_로그로_치지_않는다(tmp_path):
    root = _project(tmp_path)
    (root / "work_log" / "Phase_002.md").write_text("# 로그\n", encoding="utf-8")
    text = build_context(root)
    assert "도 읽을 것" not in text
    assert "로그 파일은 없다" in text


def test_실테스트_대기는_건수와_함께_플레이스홀더를_빼고_싣는다(tmp_path):
    text = build_context(_project(tmp_path))
    assert "사용자 실테스트 대기 2건" in text
    assert "오프라인에서 검색" in text
    assert "플레이스홀더" not in text
    assert "이 줄은 실테스트 대기가 아니다" not in text


def test_미초기화_템플릿이면_초기화가_먼저라고만_한다(tmp_path):
    root = _project(tmp_path, claude_md="<!-- ★★★ 템플릿 초기화 모드 ★★★ -->\n")
    text = build_context(root)
    assert "미초기화" in text
    assert "현재 Phase" not in text


def test_plan이_없으면_없다고_알린다(tmp_path):
    assert "plan.md가 없다" in build_context(_project(tmp_path, plan=None))


def test_전부_완료면_현재_Phase를_지어내지_않는다(tmp_path):
    plan = PLAN.replace("- [ ] Phase", "- [x] Phase")
    assert "미완료 Phase가 없다" in build_context(_project(tmp_path, plan=plan))


# ── 훅을 실제로 돌려 본다 — cp949 콘솔 조건(test_hook_io.py와 같은 이유) ─────────

def test_cp949_콘솔에서도_주입이_ASCII로_나간다():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"
    env.pop("PYTHONUTF8", None)
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input="{}", capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert p.returncode == 0, f"훅이 죽었다 — 주입이 조용히 사라진다:\n{p.stderr}"
    assert p.stdout.isascii()
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "[session_context]" in out["additionalContext"]   # 할 말이 없어도 한 줄은 낸다
