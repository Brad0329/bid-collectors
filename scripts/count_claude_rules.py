"""CLAUDE.md의 규칙 개수를 세어 상한과 비교한다 (Phase 종료 점검용).

## 왜 바이트가 아니라 개수인가 (2026-09-05 교체)

준수율을 떨어뜨리는 것은 파일 크기가 아니라 **지시문 개수**다. 최상위 모델도 지시문 150~200개
부근부터 준수율이 급락하고 앞쪽 지시문을 더 잘 지킨다(IFScale, arXiv 2507.11538). Claude Code
세션 1,650건을 분석한 요인 연구(arXiv 2605.10039)에서는 설정 파일 크기가 준수율에 효과가 없었다.
Claude Code 시스템 프롬프트가 이미 약 50개를 쓰므로 CLAUDE.md 예산은 100개 안팎이다.
종전 "약 15KB" 상한은 한글 3바이트 문자에서 개수를 반영하지 못하는 대리 지표였다.

## 세는 규칙 (CLAUDE.md '비대화 방지' 절과 같아야 한다)

- 글머리(`- `)·번호(`1. `)·표의 데이터 행 하나 = 규칙 하나. 표 머리행·구분행은 제외.
- `<...>` 플레이스홀더가 든 줄은 제외 — 아직 규칙이 아니라 빈칸이다.
- 초기화 블록(`초기화 블록 끝` 표시 앞)은 제외 — 초기화가 끝나면 삭제되는 부분이다.
- 한 줄에 규칙이 둘 있어도 하나로 센다. 정확한 값이 아니라 **재현 가능한 값**이 목적이다.

개수와 함께 **만료 표시(`EXPIRY_MARKER`)가 붙은 줄을 출력한다** — 한 번 통과하면 끝나는 게이트는
조건이 채워졌을 때 지워야 하는데, 지울 시점을 기억하는 사람이 없다. Phase 종료 점검에 묻어간다.

사용법:
  python scripts/count_claude_rules.py            # 저장소의 CLAUDE.md, 상한 100
  python scripts/count_claude_rules.py --cap 80   # 다른 상한
  python scripts/count_claude_rules.py <경로>      # 다른 파일

넘으면 exit 1. `tools_tests/test_count_claude_rules.py`가 실제 CLAUDE.md로 이 검사를 돌린다 —
pytest가 빨간불이면 그것이 이관 신호다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAP = 100
INIT_END_MARKER = "초기화 블록 끝"
# 한 번 통과하면 끝나는 게이트에 붙는 표시 — 조건이 채워지면 그 문장을 지운다(CLAUDE.md '비대화 방지')
EXPIRY_MARKER = "⏳만료:"

BULLET = re.compile(r"^\s*(- |\d+\. )")
TABLE_ROW = re.compile(r"^\s*\|")
TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:\-|]+\|?\s*$")
PLACEHOLDER = re.compile(r"<[^<>\n]+>")


def rule_lines(text: str) -> list[str]:
    """규칙으로 세는 줄만 돌려준다. 테스트가 이 함수를 직접 부른다."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if INIT_END_MARKER in line:
            lines = lines[i + 1:]
            break
    out: list[str] = []
    for i, line in enumerate(lines):
        if PLACEHOLDER.search(line):
            continue
        if BULLET.match(line):
            out.append(line)
        elif TABLE_ROW.match(line) and not TABLE_SEPARATOR.match(line):
            # 머리행 = 바로 다음 줄이 구분행인 표 행
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not TABLE_SEPARATOR.match(nxt):
                out.append(line)
    return out


def expiry_lines(text: str) -> list[str]:
    """만료 표시가 붙은 줄(초기화 블록 뒤). 글머리가 아닌 이어지는 줄에도 붙으므로 전 줄을 훑는다."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if INIT_END_MARKER in line:
            lines = lines[i + 1:]
            break
    return [line.strip() for line in lines if EXPIRY_MARKER in line]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=str(ROOT / "CLAUDE.md"))
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"파일이 없다: {path}")
    text = path.read_text(encoding="utf-8")
    n = len(rule_lines(text))
    if n == 0:
        # 0건은 '깨끗함'이 아니라 실패다 — 형식이 바뀌었거나 엉뚱한 파일을 본 것이다.
        raise SystemExit(f"★ 규칙을 한 건도 세지 못했다: {path}")

    verdict = "초과 — 이관할 것" if n > args.cap else "이내"
    print(f"[{path.name}] 규칙 {n}개 / 상한 {args.cap}개 → {verdict}")
    expiring = expiry_lines(text)
    if expiring:
        print(f"  만료 표시 {len(expiring)}개 — 조건이 채워졌으면 그 문장을 지운다:")
        for line in expiring:
            print(f"    {line}")
    if n > args.cap:
        print("  이관 절차: CLAUDE.md 'CLAUDE.md 비대화 방지' ①②③")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
