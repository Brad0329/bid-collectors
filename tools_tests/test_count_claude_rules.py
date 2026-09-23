"""규칙 개수 측정기(`scripts/count_claude_rules.py`) 검증 + 실제 CLAUDE.md 상한 검사.

마지막 테스트가 이 파일의 존재 이유다 — **실제 CLAUDE.md가 상한을 넘으면 pytest가 빨간불이
된다.** 문서에 적힌 상한은 지켜지지 않았다(15KB 상한을 넘긴 채 커밋된 실사례, 2026-09-05).
구조로 강제한다.

실행: python -m pytest tools_tests/test_count_claude_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from count_claude_rules import DEFAULT_CAP, ROOT, expiry_lines, rule_lines  # noqa: E402


def test_글머리와_번호를_센다():
    assert len(rule_lines("- a\n- b\n1. c\n  - d\n본문 줄\n")) == 4


def test_표는_데이터_행만_센다():
    text = "| 게이트 | 도구 |\n|---|---|\n| a | b |\n| c | d |\n"
    assert len(rule_lines(text)) == 2


def test_플레이스홀더_줄은_세지_않는다():
    assert len(rule_lines("- 규칙\n- <여기 채울 것>\n- `<명령>`으로 돌린다\n")) == 1


def test_초기화_블록은_세지_않는다():
    text = "- 초기화 1\n- 초기화 2\n<!-- 초기화 블록 끝 -->\n- 진짜 규칙\n"
    assert rule_lines(text) == ["- 진짜 규칙"]


def test_만료_표시는_이어지는_줄에서도_찾고_초기화_블록은_뺀다():
    text = ("- 초기화 ⏳만료: 이건 빼야 한다\n<!-- 초기화 블록 끝 -->\n"
            "- 규칙\n  ⏳만료: 채워지면 지운다\n본문 ⏳만료: 글머리 아님\n- 모래시계(⏳) 설명 줄\n")
    assert expiry_lines(text) == ["⏳만료: 채워지면 지운다", "본문 ⏳만료: 글머리 아님"]


def test_실제_CLAUDE_md가_상한_이내다():
    """★ 여기가 게이트다. 빨간불이면 규칙을 더 넣지 말고 이관한다."""
    n = len(rule_lines((ROOT / "CLAUDE.md").read_text(encoding="utf-8")))
    assert 0 < n <= DEFAULT_CAP, f"CLAUDE.md 규칙 {n}개 > 상한 {DEFAULT_CAP}개 — 이관할 것"
