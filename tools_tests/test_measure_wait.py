"""벽시계 측정기(`scripts/measure_wait.py`) 검증.

**이 스크립트의 위험은 죽는 것이 아니라 그럴듯하게 틀리는 것이다** — 자매 스크립트
(`measure_approvals.py`)와 같은 성질이다. 짝을 잘못 맞추거나 조용히 빠뜨려도 표는 정상으로
보이고, **"승인 대기가 없다"는 거짓 결론**이 나온다. 그래서 짝짓기 규칙을 직접 잰다.

특히 지키는 것:
  ① `tool_use`↔`tool_result`를 **id로** 짝짓는다 (순서로 짝지으면 병렬 호출에서 뒤섞인다)
  ② 짝이 없는 `tool_use`는 **버린다** (세션이 중간에 끊기면 마지막 호출이 그렇다)
  ③ 재는 도구만 본다 (Read·Grep까지 세면 합계가 부풀려져 비율이 뭉개진다)

실행: python -m pytest tools_tests/test_measure_wait.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from measure_wait import measure  # noqa: E402


def _use(tool_id: str, ts: str, name: str = "PowerShell", **payload) -> dict:
    return {
        "timestamp": ts,
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": payload}
            ]
        },
    }


def _result(tool_id: str, ts: str) -> dict:
    return {
        "timestamp": ts,
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id}]},
    }


def _session(tmp_path: Path, events: list[dict]) -> list[Path]:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8"
    )
    return [p]


def test_간격을_초로_돌려준다(tmp_path):
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", command="git status"),
                _result("a", "2026-08-20T10:00:30.500Z"),
            ],
        )
    )
    assert rows == [(30.5, "PowerShell", "git status")]


def test_짝은_순서가_아니라_id로_맞춘다(tmp_path):
    """★ 순서로 짝지으면 **긴 대기가 짧은 호출에 붙어** 원인을 엉뚱한 곳으로 보낸다."""
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", command="느린 것"),
                _use("b", "2026-08-20T10:00:01.000Z", command="빠른 것"),
                _result("b", "2026-08-20T10:00:02.000Z"),  # b가 먼저 끝난다
                _result("a", "2026-08-20T10:00:40.000Z"),
            ],
        )
    )
    assert dict((cmd, sec) for sec, _, cmd in rows) == {"느린 것": 40.0, "빠른 것": 1.0}


def test_짝이_없는_호출은_버린다(tmp_path):
    """세션이 중간에 끊기면 마지막 `tool_use`에 결과가 없다. 0초로 세면 안 된다."""
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", command="끝난 것"),
                _result("a", "2026-08-20T10:00:05.000Z"),
                _use("b", "2026-08-20T10:00:06.000Z", command="안 끝난 것"),
            ],
        )
    )
    assert [cmd for _, _, cmd in rows] == ["끝난 것"]


def test_재는_도구만_본다(tmp_path):
    """Read·Grep까지 세면 합계가 부풀려져 '느린 것의 비율'이 뭉개진다."""
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", name="Read", file_path="x.md"),
                _result("a", "2026-08-20T10:00:09.000Z"),
                _use("b", "2026-08-20T10:00:10.000Z", name="Bash", command="ls"),
                _result("b", "2026-08-20T10:00:11.000Z"),
            ],
        )
    )
    assert [tool for _, tool, _ in rows] == ["Bash"]


def test_Write는_본다(tmp_path):
    """보호 디렉토리에 쓸 때 확인을 물어 **실측 137초**가 나온 적이 있다(`.git/`)."""
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", name="Write", file_path="a.txt"),
                _result("a", "2026-08-20T10:02:00.000Z"),
            ],
        )
    )
    assert rows == [(120.0, "Write", "a.txt")]


def test_명령의_줄바꿈을_한_줄로_눕힌다(tmp_path):
    """여러 줄 명령이 표를 깨뜨리면 느린 순 목록을 못 읽는다."""
    rows = measure(
        _session(
            tmp_path,
            [
                _use("a", "2026-08-20T10:00:00.000Z", command="git commit \\\n  -F x"),
                _result("a", "2026-08-20T10:00:01.000Z"),
            ],
        )
    )
    assert "\n" not in rows[0][2]


def test_깨진_줄이_있어도_나머지를_센다(tmp_path):
    """트랜스크립트를 쓰는 중이면 마지막 줄이 잘려 있을 수 있다."""
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(_use("a", "2026-08-20T10:00:00.000Z", command="ls"))
        + "\n"
        + json.dumps(_result("a", "2026-08-20T10:00:03.000Z"))
        + '\n{"timestamp": "2026-08-2',
        encoding="utf-8",
    )
    assert measure([p]) == [(3.0, "PowerShell", "ls")]
