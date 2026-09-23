"""승인 대기를 **벽시계로** 잰다 — `measure_approvals.py`의 예측을 검증하는 쪽.

## 왜 따로 있나

`measure_approvals.py`는 접두사 매처로 "승인이 필요했을 것"을 **예측**한다. 그 예측이
hanjadic 2026-08-20 실측에서 **양쪽으로 틀렸다**:

- **과대**: `git add`·`git commit -F`를 승인 필요로 셌다. 실제로는 1.6초 이하로 통과한다.
- **과소**: 정작 **자기 자신을 부르는 명령**(인자가 붙은 형태)이 30초를 물고 있었는데 못 짚었다.

그래서 판정은 여기서 한다. **명령 자체 시간으로 설명되지 않는 초가 승인 대기다.**
예측은 어디를 볼지 고르는 데 쓰고, 줄었는지 여부는 이 숫자로 확인한다.

## 재는 것

트랜스크립트의 `tool_use`와 짝 `tool_result`의 **timestamp 간격**이다. 승인 창이 떠 있는
동안 도구는 아직 실행되지 않았으므로, 그 대기가 이 간격에 그대로 들어온다.

**★ 같은 명령을 전후로 비교하는 것이 가장 확실하다.** 절대값에는 명령 자체 시간이
섞여 있다(측정기 스크립트는 그 자체로 4~6초다). 실측 예: 같은 호출이 승인 상태 변화
전후로 36.3초 → 4.3초 — 그 27초가 승인 대기였다.
**단, 그 변화의 원인이 "내가 넣은 규칙"인지 "사용자가 누른 항상 허용"인지는 벽시계로
못 가른다** — 그 판정은 `settings.local.json`에 **새로** 적혔는지로 한다
(hanjadic이 이 오귀인으로 판정을 두 번 뒤집었다).

사용법:
  python scripts/measure_wait.py                    # 가장 최근 세션
  python scripts/measure_wait.py --sessions 3       # 최근 3개 합산
  python scripts/measure_wait.py --grep pytest      # 명령에 이 말이 든 것만
  python scripts/measure_wait.py --slow 8           # 이 초를 넘으면 표시 (기본 8)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 세션 찾기는 measure_approvals와 **같은 규칙이어야 한다** — 두 스크립트가 다른 세션을 보면
# 예측과 실측을 대조할 수 없다. 그래서 복사하지 않고 가져다 쓴다. 출력 인코딩도 같은 이유로
# 한 곳에서 가져온다 — 여기만 빠져 있어 cp949 콘솔에서 죽었다(vanasso.kr 2026-09-06).
from measure_approvals import find_sessions, utf8_stdout  # noqa: E402

# 도구 이름. 셸 말고 Write/Edit도 본다 — 보호 디렉토리에 쓸 때 확인을 물어서
# 실제로 137초가 나온 적이 있다(`.git/` 안의 커밋 메시지 파일, hanjadic 2026-08-19).
TOOLS = ("Bash", "PowerShell", "Write", "Edit")


def _label(name: str, payload: dict) -> str:
    """무엇을 부른 것인지 한 줄로. 명령이 없으면 대상 파일 경로를 쓴다."""
    raw = payload.get("command") or payload.get("file_path") or ""
    return " ".join(str(raw).split())


def measure(paths: list[Path]) -> list[tuple[float, str, str]]:
    """[(초, 도구, 명령)] — tool_use와 짝 tool_result의 간격."""
    pending: dict[str, tuple[str, str, str]] = {}
    rows: list[tuple[float, str, str]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                # 트랜스크립트가 쓰이는 중이면 마지막 줄이 잘려 있을 수 있다.
                # 그 한 줄만 버리고 계속한다.
                continue
            content = (ev.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            ts = ev.get("timestamp")
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in TOOLS:
                    pending[block["id"]] = (
                        ts,
                        block["name"],
                        _label(block["name"], block.get("input") or {}),
                    )
                elif block.get("type") == "tool_result":
                    hit = pending.pop(block.get("tool_use_id"), None)
                    if hit is None or not (hit[0] and ts):
                        continue
                    t0, tool, cmd = hit
                    delta = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    ) - datetime.fromisoformat(t0.replace("Z", "+00:00"))
                    rows.append((delta.total_seconds(), tool, cmd))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="세션 ID 또는 .jsonl 경로 (기본: 가장 최근)")
    ap.add_argument("--sessions", type=int, default=1, help="최근 N개 세션 합산 (기본 1)")
    ap.add_argument("--grep", help="명령에 이 문자열이 든 호출만 본다")
    ap.add_argument("--slow", type=float, default=8.0, help="이 초를 넘으면 표시 (기본 8)")
    ap.add_argument("--top", type=int, default=25, help="느린 순으로 몇 줄까지 (기본 25)")
    args = ap.parse_args()

    utf8_stdout()

    paths = find_sessions(args.session, args.sessions)
    print("[대상 세션]")
    for p in paths:
        print(f"  {p.name}  ({p.stat().st_size / 1_000_000:.1f} MB)")

    rows = measure(paths)
    # **하나도 못 찾으면 '깨끗함'이 아니라 실패다.** 초록색으로 보이면서 아무것도 안 재는
    # 상태를 만들지 않는다(measure_approvals와 같은 규칙).
    if not rows:
        print("\n호출을 하나도 찾지 못했다. 세션이 맞는지 확인해라.", file=sys.stderr)
        return 1

    if args.grep:
        rows = [r for r in rows if args.grep in r[2]]
        if not rows:
            print(f"\n'{args.grep}'가 든 호출이 없다.", file=sys.stderr)
            return 1

    rows.sort(reverse=True)
    slow = [r for r in rows if r[0] > args.slow]
    total = sum(r[0] for r in rows)
    print(
        f"\n[규모] {len(rows)}건 / 합계 {total:.0f}초 / "
        f"{args.slow:g}초 초과 **{len(slow)}건**"
    )
    if slow:
        print(f"  느린 것 합계 {sum(r[0] for r in slow):.0f}초 "
              f"— 전체의 {sum(r[0] for r in slow) / total * 100:.0f}%")

    print("\n[느린 순]")
    for sec, tool, cmd in rows[: args.top]:
        mark = "  <-- 확인 대기 의심" if sec > args.slow else ""
        print(f"  {sec:7.1f}s  {tool:<10} {cmd[:70]}{mark}")
    if len(rows) > args.top:
        print(f"  … {len(rows) - args.top}건 더 (--top 으로 늘린다)")

    print("\n[읽는 법]")
    print("  ★ 절대값에는 **명령 자체 시간**이 섞여 있다. 규칙을 고쳤으면")
    print("    `--grep <명령>`으로 **같은 호출의 전후**를 비교해라 — 그 차이가 승인 대기다.")
    print("  ★ 다만 그 차이의 원인(내 규칙 vs 사용자의 '항상 허용')은 벽시계로 못 가른다.")
    print("    판정은 settings.local.json에 **새로** 적혔는지로 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
