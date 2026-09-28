r"""승인 창이 뜨는 순간을 기록한다 (PermissionRequest 훅 — 막지도 허용하지도 않는다).

**왜 훅인가**: 승인 대기 측정기(`scripts/measure_approvals.py`·`measure_wait.py`)는 지금까지 **추측**이었다 —
접두사 매처로 "물었을 것"을 예측하거나, 호출↔결과 간격이 8초를 넘으면 "대기였을 것"으로 봤다. 명령 자체가
50초 걸린 것과 승인 창이 50초 떠 있던 것을 가를 수 없어 보고서마다 단서를 달아야 했다.
PermissionRequest는 공식 문서상 "Claude Code가 사용자에게 권한을 물으려는 순간에만" 돈다
(PreToolUse는 모든 호출에 돈다 — code.claude.com/docs/en/hooks, 2026-09-28 원문 확인). 여기서 한 줄씩 적으면
**실제로 물은 호출의 정확한 목록**이 남는다. (템플릿 fca5c54에서 가져옴 — 노하우_승인_대기_최소화.md [H17])

**무엇을 적나** (`.claude/permission_requests.jsonl`, gitignore — 사용자 데이터라 커밋하지 않는다):
  시각(UTC)·session_id·서브에이전트 여부(agent_id/agent_type)·permission_mode·tool_name·
  명령/경로/URL·Claude Code가 제안한 규칙(permission_suggestions — 어떤 규칙을 열면 안 묻는지의 정답 후보).
  명령 원문을 적으므로 비밀이 든 명령이면 그것도 적힌다 — 트랜스크립트에 이미 있는 것과 같은 노출이고
  파일은 로컬·gitignore다. 명령에 비밀(API 키)을 넣지 않는 것이 먼저다(CLAUDE.md 불변 규칙).

**출력하지 않는다** — stdout이 비고 exit 0이면 "결정 없음"이라 승인 흐름은 그대로다(문서 'Exit code 0').
**기록에 실패해도 승인 창을 막지 않는다** — 이유는 stderr로(exit 0이면 디버그 로그로만 간다). 그래서 실패는
측정기가 "기록 0건/파일 없음"으로 드러낸다(조용히 추측으로 넘어가지 않는다).

루트: `CLAUDE_PROJECT_DIR`(없으면 이 파일 위치). 입력의 `cwd`는 쓰지 않는다 — 하위 폴더로 옮겨 가면 따라 바뀐다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from hook_io import read_input

LOG_NAME = "permission_requests.jsonl"


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def record(data: dict) -> dict:
    """훅 입력 → 기록할 한 줄. 테스트가 이 함수를 직접 부른다."""
    tool_input = data.get("tool_input") or {}
    target = (tool_input.get("command") or tool_input.get("file_path")
              or tool_input.get("url") or tool_input.get("pattern") or "")
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": data.get("session_id"),
        "agent_id": data.get("agent_id"),
        "agent_type": data.get("agent_type"),
        "permission_mode": data.get("permission_mode"),
        "tool_name": data.get("tool_name"),
        "target": target,
        "suggestions": [   # settings 규칙 문법 그대로: `Bash(npm run lint)` / 도구 통째면 `WebFetch`
            f"{r.get('toolName')}({r['ruleContent']})" if r.get("ruleContent") is not None
            else r.get("toolName")
            for s in data.get("permission_suggestions") or []
            if s.get("type") == "addRules" and s.get("behavior") == "allow"
            for r in s.get("rules") or []
        ],
    }


def main() -> int:
    data = read_input("log_permission_request")   # stdin을 UTF-8 바이트로 읽는다 — hook_io 참조
    if data is None:
        return 0
    try:
        line = json.dumps(record(data), ensure_ascii=False)
        path = project_root() / ".claude" / LOG_NAME
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # 기록 실패로 승인 창을 막지 않는다. 이유는 남긴다 — 측정기가 '기록 없음'으로 드러낸다.
        try:
            print(f"log_permission_request: 기록 실패로 넘어간다 ({e!r})", file=sys.stderr)
        except Exception:
            pass  # stderr조차 못 쓰는 콘솔(cp949 등)이면 더 알릴 길이 없다 — 측정기가 0건으로 드러낸다
    return 0


if __name__ == "__main__":
    sys.exit(main())
