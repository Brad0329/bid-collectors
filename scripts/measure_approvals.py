"""승인 대기의 원인을 **트랜스크립트에서 세어** 분류한다 (Phase 종료 점검용).

세션이 느리게 느껴질 때 줄여야 할 것은 대개 테스트가 아니라 승인 대기다
(hanjadic 실측: 23.4분 세션에서 테스트 30초 / 승인 대기 약 6분).
그런데 **원인을 나눠 세지 않으면 엉뚱한 처방을 한다** — kanadic 2026-08-19 실측에서
`cd` 접두사만 없애면 160→111, 읽기 전용 규칙까지 더해야 →16이었다.
규칙만 늘렸으면 3분의 1도 못 줄였다.

## 이 스크립트가 지키는 것

- **원인을 세 갈래로 가른다.** 처방이 서로 다르기 때문이다:
  ① 호출 형태(내 습관 — 규칙을 넣을 일이 아니다) ② 읽기 전용인데 규칙 없음(넣어도 된다)
  ③ 상태를 바꾸는 명령(사람이 **비파괴/파괴**를 가른다 — 비파괴는 반복되면 연다, 파괴는 안 연다)
- **③을 자동 제안하지 않는다.** 이 분석기는 비파괴와 파괴를 못 가른다(가르려면 deny 패턴
  대조가 필요하다). 그래서 ③은 건수만 보여 주고, 어느 것을 열지는 사람이 CLAUDE.md
  '허용의 기준은 위험이다'에 따라 정한다. 규칙을 열기 전에 **이미 열린 형태로 불렀는지**부터
  본다 — `git add -A`가 열려 있는데 `git add <경로>`로 불러 물은 실사례(2026-08-22).
- **표본이 모자라면 처방하지 않는다**(`--min-calls`, 기본 100). 짧은 Phase의 노이즈에
  규칙을 맞추면 규칙만 늘고 효과는 없다.
- **호출을 하나도 못 찾으면 '깨끗함'이 아니라 실패다**(exit 1). 초록색으로 보이면서
  아무것도 안 재는 상태를 만들지 않는다(silent failure 금지).

## 결과는 예측이지 실측이 아니다

여기 쓰는 접두사 매처는 실제 승인 엔진의 **근사치**다. 그래서 규칙을 넣은 뒤에는
**다시 돌려** 예측이 맞았는지 확인해야 한다.

**★ 규칙은 즉시 걸린다 — 재시작을 기다리지 않는다** (hanjadic 2026-08-29 확정: local에 규칙을
넣자마자 같은 세션의 다음 보고서에서 그 명령이 사라졌다. 종전의 "미적용"·"갈렸다" 오진들은
죽은 settings.json에 넣었거나 벽시계로 판정한 것이 원인이었다). 반영 확인은 그 자리에서:
① 실제로 묻던 명령으로 재검증 ② **판정은 `settings.local.json`에 그 명령이 새로 적혔는지로**
(벽시계로는 못 가른다 — "한 번만 허용"은 아무것도 안 남기는 것도 주의).

**★ 이 스크립트의 예측을 그대로 믿지 말 것.** 같은 실측에서 두 방향으로 다 틀렸다.
- **과대**: `git add`/`git commit -F`를 승인 필요로 셌지만 실제로는 1.6초 이하로 통과한다.
- **과소**: 정작 **자기 자신을 부르는 명령**(인자가 붙은 형태)이 30초를 물고 있었는데 못 짚었다.
→ 규칙을 고친 뒤에는 **호출별 벽시계**(`scripts/measure_wait.py` — tool_use↔tool_result 간격)로
  확인한다. 그것이 실측이고, 아래 수치는 어디를 볼지 고르는 데만 쓴다.

**★★ 파이프라인의 꼬리를 원인으로 지목하면 그것은 오진이다** (hanjadic 2026-08-20 실측).
이 스크립트는 `|`·`;`로 조각내 조각마다 head를 세는데, **PowerShell 도구는 앞머리 하나로
줄 전체를 판정한다** — `git log | Out-File <경로>`가 안 물었다(파일을 쓰는 cmdlet인데도).
그래서 `Select-Object`·`Select-String`이 "남은 최대 원인"으로 두 번 보고됐지만 **둘 다
가짜였고**, 규칙을 넣었다면 한 건도 안 줄었다. **꼬리가 상위에 뜨면 그 줄의 앞머리를 봐라.**
(Bash 도구도 같은지는 재보지 않았다.)

사용법:
  python scripts/measure_approvals.py                 # 가장 최근 세션
  python scripts/measure_approvals.py --sessions 3    # 최근 3개 세션 합산
  python scripts/measure_approvals.py --session <세션ID 또는 .jsonl 경로>
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── 원인 ① 호출 형태 ────────────────────────────────────────────────────────
# 저장소 루트로 가는 cd. 작업 디렉토리는 이미 루트이고 호출 사이에 유지되므로 이 cd는 하는 일이
# 없는데, 맨 앞에 붙는 순간 어떤 접두사 규칙에도 안 걸린다.
#
# ★ 저장소 경로는 하드코딩하지 않고 ROOT에서 만든다 — 이 파일은 프로젝트 간에 복사된다.
#   (원본 kanadic판은 경로가 박혀 있어 이식할 때 이 자리를 고쳤다.)
#
# ★ **명령 전체를 볼 때와 조각을 볼 때 패턴이 달라야 한다.** 조각에는 `&&`가 남아 있지 않다
#   — 쪼갤 때 구분자로 먹히기 때문이다. 처음에 하나로 쓰다가 "cd 형태 0건"이라는 거짓 결과를
#   냈다(kanadic 2026-08-19). 원인 분류가 통째로 뒤집히는 자리라 테스트가 두 형태를 다 검사한다.


def _repo_cd_pattern(root: Path) -> str:
    comps = [c for c in re.split(r"[\\/]+", str(root)) if c]
    first = comps[0]
    if re.fullmatch(r"[A-Za-z]:", first):  # Windows 드라이브 문자 — 대소문자 모두 받는다
        d = first[0]
        # `C:/…`뿐 아니라 Git Bash(MSYS)의 `/c/…`도 같은 루트다 (vanasso.kr 2026-09-06: 20건 통과)
        head = f"(?:[{d.upper()}{d.lower()}]:|/[{d.upper()}{d.lower()}])"
    else:
        head = re.escape(first)
    body = "[\\\\/]".join(re.escape(c) for c in comps[1:])
    return rf"""^\s*cd\s+["']?{head}[\\/]{body}[\\/]?["']?"""


_REPO_CD = _repo_cd_pattern(ROOT)
# `cd <루트> 2>/dev/null; …`처럼 경로 뒤에 리다이렉트를 끼운 형태 (vanasso.kr 2026-09-06: 49건 통과)
_REDIRECTS = r"(?:\s+\d?[<>]{1,2}\S*)*"
REDUNDANT_CD_SEGMENT = re.compile(_REPO_CD + _REDIRECTS + r"\s*$")
# 뒤에 `&&`·`;`가 오거나, **구분자 없이 바로 다른 낱말**이 오는 형태(`cd <루트> ls` — bash는
# "too many arguments"로 죽어 ls는 돌지도 않는다. vanasso.kr 2026-09-06: 99건 통과).
REDUNDANT_CD_COMMAND = re.compile(_REPO_CD + _REDIRECTS + r"(?:\s*(?:&&|;)|\s+\S)")

# ── 원인 ② 읽기 전용 ────────────────────────────────────────────────────────
# 상태를 바꾸지 않는 명령만. **여기 없는 것은 자동 제안하지 않는다**(모르면 안 여는 쪽).
SAFE_READONLY = {
    "grep", "egrep", "rg", "sed", "awk", "head", "tail", "sort", "uniq", "wc", "cut",
    "tr", "echo", "cat", "ls", "basename", "dirname", "date", "xxd", "od",
    "jq", "diff", "file", "stat", "printf", "column", "nl", "pwd", "which", "whoami",
}

# 위 명령을 **상태 변경으로 뒤집는** 플래그. 하나라도 있으면 읽기 전용이 아니다.
# (`sed -i`가 대표적이다 — 이걸 안 보면 파일을 고치는 명령에 규칙을 열어 주게 된다.)
MUTATING_FLAGS = {
    "sed": {"-i", "--in-place"},
    "find": {"-delete", "-exec", "-execdir", "-ok"},
    "sort": {"-o", "--output"},
    "jq": {"-i"},
}

# 파일을 만들거나 지우는 셸 연산자. 읽기 전용 명령이라도 이게 붙으면 상태를 바꾼다.
WRITES_TO_FILE = re.compile(r"(?<![0-9])>{1,2}(?![&])")

# ── 원인 ③ 판단 필요 ────────────────────────────────────────────────────────
# 자동 제안에서 **영구 제외**한다. 규칙을 여는 것 자체가 방어를 갉아먹는 부류.
NEVER_SUGGEST = {
    "rm", "rmdir", "mv", "dd", "chmod", "chown", "kill", "pkill", "shutdown",
    "pip", "pip3", "npm", "yarn", "wget", "tar", "unzip", "curl",
}

# 셸 제어문. 규칙을 넣을 대상이 **아니다** — `for … do … done`은 조각마다 검사에 걸려
# 한 호출이 여러 번 막힌다. 처방은 "규칙 추가"가 아니라 **파이썬으로 옮기기**다.
SHELL_KEYWORDS = {"for", "do", "done", "while", "if", "then", "else", "elif", "fi",
                  "case", "esac", "[", "[[", "}"}


# ── 출력 ────────────────────────────────────────────────────────────────────

def utf8_stdout() -> None:
    """콘솔 인코딩과 무관하게 보고서를 찍는다 — 측정기 2종이 **같이** 쓴다.

    vanasso.kr 2026-09-06 실측: 이 한 줄이 `measure_approvals`에만 있고 `measure_wait`에는 없어
    Bash 도구(cp949, `PYTHONIOENCODING` 없음)에서 `measure_wait`가 `—`(U+2014)에서 죽었다.
    `hook_io.py`가 고친 훅 사고와 같은 뿌리를 측정기는 한쪽만 고친 것. 그래서 한 곳으로 모은다
    (CLAUDE.md '같은 규칙이 두 곳 이상에 구현되면'). 회귀: `tools_tests/test_measure_scripts_encoding.py`.
    """
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── 트랜스크립트 ────────────────────────────────────────────────────────────

def transcript_dir() -> Path:
    """Claude Code가 이 프로젝트의 세션을 두는 곳."""
    override = os.environ.get("CLAUDE_TRANSCRIPT_DIR")
    if override:
        return Path(override)
    slug = str(ROOT).replace(":", "-").replace("\\", "-").replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def find_sessions(spec: str | None, count: int) -> list[Path]:
    d = transcript_dir()
    if spec:
        p = Path(spec)
        if p.exists():
            return [p]
        p = d / f"{spec}.jsonl"
        if p.exists():
            return [p]
        raise SystemExit(f"세션을 찾지 못했다: {spec}\n  찾아본 곳: {d}")
    if not d.exists():
        raise SystemExit(
            f"트랜스크립트 디렉토리가 없다: {d}\n"
            f"  CLAUDE_TRANSCRIPT_DIR 환경변수로 경로를 알려줄 수 있다.")
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"세션 파일(.jsonl)이 없다: {d}")
    return files[:count]


def shell_calls(paths: list[Path]) -> list[tuple[str, str]]:
    """[(도구명, 명령)] — Bash/PowerShell 호출만."""
    out: list[tuple[str, str]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue  # 잘린 줄. 세션 파일 끝에서 실제로 생긴다
            msg = obj.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if (isinstance(c, dict) and c.get("type") == "tool_use"
                        and c.get("name") in ("Bash", "PowerShell")):
                    cmd = (c.get("input") or {}).get("command", "")
                    if cmd:
                        out.append((c["name"], cmd))
    return out


# ── 명령 쪼개기 ─────────────────────────────────────────────────────────────

def split_segments(command: str) -> list[str]:
    """따옴표 **밖의** `&&`·`||`·`|`·`;`·줄바꿈에서만 쪼갠다.

    ★ 따옴표 안까지 쪼개면 `python -c "a | b"` 안의 파이프가 조각으로 잡혀 수치가 부풀려진다
      (kanadic 2026-08-19에 실제로 그렇게 잘못 셌다). heredoc 본문도 같은 이유로 잘라낸다.
    """
    body = command.split("<<")[0]
    out: list[str] = []
    cur = ""
    quote: str | None = None
    depth = 0  # `$(...)` 안은 쪼개지 않는다 — `old=$(git show ...)`가 두 조각으로 갈렸다
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == quote:
                quote = None
            cur += ch
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif body.startswith("$(", i):
            depth += 1
            cur += "$("
            i += 2
            continue
        elif ch == ")" and depth:
            depth -= 1
            cur += ch
        elif depth:
            cur += ch
        elif body.startswith("&&", i) or body.startswith("||", i):
            out.append(cur)
            cur = ""
            i += 2
            continue
        elif ch in "|;\n":
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return [s.strip() for s in out if s.strip()]


def head_command(segment: str) -> str:
    """조각의 실행 파일 이름. 경로가 붙어 있으면 마지막 요소만."""
    first = segment.split()[0] if segment.split() else ""
    return first.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")


# ── 허용 규칙 ───────────────────────────────────────────────────────────────

def load_rules() -> dict[str, list[str]]:
    """{'Bash': [...], 'PowerShell': [...]} — **`settings.local.json`만** 읽는다.

    `settings.json`의 `allow`는 이 환경에서 효력이 없다(hanjadic 2026-08-22 실측 — 세션 전부터
    있던 규칙이 물었다). 거기 적힌 규칙을 세면 "허용됐을 것"을 과대 예측한다.
    """
    rules: dict[str, list[str]] = {"Bash": [], "PowerShell": []}
    seen: set[str] = set()
    for name in ("settings.local.json",):
        path = ROOT / ".claude" / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for rule in data.get("permissions", {}).get("allow", []):
            if rule in seen:
                continue
            seen.add(rule)
            for tool in rules:
                prefix = f"{tool}("
                if rule.startswith(prefix) and rule.endswith(")"):
                    rules[tool].append(rule[len(prefix):-1])
    return rules


def allowed(segment: str, rules: list[str], extra: list[str] = ()) -> bool:
    """접두사 매칭. **실제 승인 엔진의 근사치다** — 결과는 예측이지 실측이 아니다.

    꼬리 `*`는 **공백이 있든 없든** 받는다:
      - `cmd *` : 맨몸 `cmd`와 `cmd <무엇이든>` (원래 지원하던 형태)
      - `cmd*`  : `cmd`로 시작하는 전부 — `cmd--flag`처럼 **공백 없이 붙는 형태**까지

      - `cmd:*` : **`cmd *`와 동등** (공식 문서: "`:*` 접미사는 꼬리 ` *`와 같다", 패턴 끝에서만)

    ★ 2026-08-22 실측: ` *`만 처리하던 시절에 규칙을 `X*`로 합치자 **분석기가 그 규칙을
    통째로 못 보고** `git commit` 13건을 '물었을 것'으로 세었다(실제로는 안 물었다).
    ★ 2026-09-06 vanasso.kr: local 규칙 97건 대부분이 `Bash(node:*)`류였는데 `:*`를 몰라
    **51건 전부 '승인 필요'라는 거짓 보고서**를 냈다 — 같은 자리에서 두 번째.
    **규칙 문법이 하네스와 여기 두 곳에 구현돼 있어 조용히 어긋나는 자리다**
    (CLAUDE.md '같은 규칙이 두 곳 이상에 구현되면'). 규칙 문법을 바꾸면 여기도 같이 본다.
    """
    for rule in list(rules) + list(extra):
        if rule.endswith(":*"):
            rule = rule[:-2] + " *"     # 공식 문법: 꼬리 `:*` == 꼬리 ` *` (가운데 콜론은 리터럴)
        if rule.endswith("*"):
            body = rule[:-1]                       # `X ` 또는 `X`
            if segment.startswith(body):
                return True
            # `X *`는 인자 없는 맨몸 `X`도 덮는다 (`X*`는 startswith로 이미 덮인다)
            if body.endswith(" ") and segment == body[:-1]:
                return True
        elif segment == rule:
            return True
    return False


# ── 분류 ────────────────────────────────────────────────────────────────────

def is_readonly(segment: str) -> bool:
    """이 조각이 상태를 바꾸지 않는가. 모르면 False(안전한 쪽)."""
    cmd = head_command(segment)
    if cmd not in SAFE_READONLY:
        return False
    tokens = segment.split()[1:]
    if any(t in MUTATING_FLAGS.get(cmd, set()) for t in tokens):
        return False
    if WRITES_TO_FILE.search(segment):
        return False  # `grep x > out.txt`는 파일을 만든다
    return True


def classify(segment: str) -> str:
    if segment.startswith("cd "):
        return "형태"
    if head_command(segment) in SHELL_KEYWORDS:
        return "셸제어문"
    if is_readonly(segment):
        return "읽기전용"
    if head_command(segment) in NEVER_SUGGEST:
        return "상태변경"
    return "판단필요"


def analyze(calls: list[tuple[str, str]], rules: dict[str, list[str]]):
    stats = {
        "total": len(calls),
        "prompted": 0,
        "cd_calls": 0,
        "causes": collections.Counter(),
        "readonly_missing": collections.Counter(),
        "readonly_as_first": collections.Counter(),  # 파이프가 아니라 파일 조회로 쓴 것
        "needs_judgment": collections.Counter(),
    }
    for tool, command in calls:
        segs = split_segments(command)
        if REDUNDANT_CD_COMMAND.match(command):
            stats["cd_calls"] += 1
        # cd를 걷어낸 뒤의 첫 조각 — 그것이 '파일 조회로 쓴 필터'인지 판정하는 기준이다
        meaningful = [s for s in segs if not s.startswith("cd ")]
        missing = [s for s in segs if not allowed(s, rules[tool])]
        if not missing:
            continue
        stats["prompted"] += 1
        for idx, seg in enumerate(missing):
            kind = classify(seg)
            stats["causes"][kind] += 1
            name = head_command(seg)
            if kind == "읽기전용":
                stats["readonly_missing"][name] += 1
                # 첫 조각이면 파이프 필터가 아니라 **파일 조회**다 → 도구로 대체할 자리
                if meaningful and seg == meaningful[0]:
                    stats["readonly_as_first"][name] += 1
            elif kind in ("판단필요", "상태변경"):
                stats["needs_judgment"][name] += 1
    return stats


def simulate(calls, rules, drop_cd: bool, extra: list[str]) -> int:
    """그 처방을 적용하면 몇 건이 남는가."""
    n = 0
    for tool, command in calls:
        segs = split_segments(command)
        if drop_cd:
            segs = [s for s in segs if not REDUNDANT_CD_SEGMENT.match(s)]
        if any(not allowed(s, rules[tool], extra if tool == "Bash" else []) for s in segs):
            n += 1
    return n


# ── 보고 ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="세션 ID 또는 .jsonl 경로 (기본: 가장 최근)")
    ap.add_argument("--sessions", type=int, default=1, help="최근 N개 세션 합산 (기본 1)")
    ap.add_argument("--min-calls", type=int, default=100,
                    help="이 미만이면 표본 부족으로 보고 처방하지 않는다 (기본 100)")
    args = ap.parse_args()

    utf8_stdout()

    paths = find_sessions(args.session, args.sessions)
    calls = shell_calls(paths)

    print("[대상 세션]")
    for p in paths:
        print(f"  {p.name}  ({p.stat().st_size / 1e6:.1f} MB)")

    if not calls:
        # 0건은 '깨끗함'이 아니다 — 파싱이 깨졌거나 엉뚱한 파일을 봤다는 뜻이다.
        raise SystemExit("\n★ 셸 호출을 한 건도 찾지 못했다. 트랜스크립트 형식이 바뀌었는지 확인해라.")

    rules = load_rules()
    st = analyze(calls, rules)

    print(f"\n[규모] 셸 호출 {st['total']}건 / 승인이 필요했을 것 **{st['prompted']}건** "
          f"({st['prompted'] / st['total'] * 100:.0f}%)")

    if st["total"] < args.min_calls:
        print(f"\n★ 표본 부족 — 호출 {st['total']}건 < 기준 {args.min_calls}건.")
        print("  짧은 구간의 노이즈에 규칙을 맞추면 규칙만 늘고 효과는 없다.")
        print("  --sessions 로 이전 세션을 합치거나, 더 쌓인 뒤에 다시 돌려라.")
        return 2

    print("\n[원인 분류] — 처방이 서로 다르므로 갈라서 센다")
    labels = {
        "형태": "호출 형태 (내 습관 — 규칙을 넣을 일이 아니다)",
        "셸제어문": "셸 제어문 for/do/done (규칙이 아니라 **파이썬으로 옮길** 자리)",
        "읽기전용": "읽기 전용인데 규칙 없음 (넣어도 된다)",
        "상태변경": "상태를 바꾸는 명령 (사람이 비파괴/파괴를 가른다 — 비파괴는 열어도 된다, 파괴는 안 연다)",
        "판단필요": "분류 밖 (사람이 판단)",
    }
    for key in ("형태", "셸제어문", "읽기전용", "상태변경", "판단필요"):
        if st["causes"][key]:
            print(f"  {st['causes'][key]:>4}회  {labels[key]}")
    print(f"  (그중 `cd <저장소 루트> &&` 형태의 호출: {st['cd_calls']}건)")

    candidates = [f"{name} *" for name, _ in st["readonly_missing"].most_common()]

    print("\n[시나리오] 처방별로 몇 건이 남는가")
    print(f"  A. 지금 그대로                     {simulate(calls, rules, False, []):>4}건")
    print(f"  B. cd 접두사만 없앴을 때           {simulate(calls, rules, True, []):>4}건")
    if candidates:
        print(f"  C. B + 읽기 전용 전부 허용 가정    {simulate(calls, rules, True, candidates):>4}건"
              "   (상한 추정용 — 아래 참조)")

    if candidates:
        print("\n[읽기 전용 후보] ★ 규칙은 settings.local.json에만 효력이 있다(settings.json의 allow는 무효).")
        print("  와일드카드는 작동하지만 패턴에 역슬래시가 있으면 죽는다 — 경로는 슬래시로.")
        print("  인자가 매번 달라지는 자리에만 `명령 *`를 좁게 열고, 나머지는 호출 문자열을 고정해라.")
        print("  PowerShell 파이프 꼬리로만 쓰인 것은 애초에 검사되지 않는다 — 앞머리를 봐라.")
        for name, n in st["readonly_missing"].most_common():
            hint = ""
            first = st["readonly_as_first"][name]
            if first:
                hint = f"   ← {first}건은 파일 조회다. Read/Grep/Glob 도구로 바꿀 자리"
            print(f"  {name:<12} # {n}회{hint}")

    if st["needs_judgment"]:
        print("\n[자동 제안하지 않음] 사람이 판단할 것 — 대개는 '묻는 게 맞다'가 정답이다")
        for name, n in st["needs_judgment"].most_common(12):
            print(f"  {n:>4}회  {name}")

    print("\n[다음 단계]")
    print("  1. '형태'가 가장 크면 규칙이 아니라 **호출 방식**을 고친다(필요하면 훅으로 강제).")
    print("  2. 규칙은 **즉시 걸린다**(2026-08-29 확정) — 그 자리에서 실제로 묻던 명령으로 재검증.")
    print("     판정은 settings.local.json에 **새로** 적혔는지로 한다.")
    print("  3. ★ 위 수치는 접두사 매처의 **예측**이고 양쪽으로 틀린 실사례가 있다(git 과대·자기 자신 과소).")
    print("     실측은 scripts/measure_wait.py (tool_use↔tool_result 간격)로 확인해라.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
