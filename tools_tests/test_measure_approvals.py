"""승인 대기 분석기(`scripts/measure_approvals.py`) 검증.

**이 테스트가 존재하는 이유는 구체적이다** — kanadic에서 만드는 동안 같은 코드에서 두 번
틀렸고, 둘 다 **틀린 채로 그럴듯한 보고서를 냈다**:

  ① 따옴표 안까지 쪼개서 `python -c "a | b"`의 파이프를 조각으로 셌다 → 원인 건수가 부풀려짐
  ② `cd` 패턴 하나를 명령과 조각에 같이 썼다. 조각에는 `&&`가 안 남으므로 영영 매칭되지 않아
     **"cd 형태 0건"**이라는 거짓 결과가 나왔다(실제로는 132건). 원인 분류가 통째로 뒤집혔다.

둘 다 예외를 내지 않고 조용히 틀린 수치를 낸다 — 이 스크립트의 위험은 죽는 것이 아니라
**그럴듯하게 틀리는 것**이다. 그래서 경계값을 직접 잰다.

★ 저장소 경로는 하드코딩하지 않는다 — 이 파일은 프로젝트 간에 복사되며, 스크립트의 ROOT를
  가져와 케이스를 만든다(그래야 어느 프로젝트에서 돌려도 그 프로젝트의 cd 패턴을 검증한다).

실행: python -m pytest tools_tests/test_measure_approvals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from measure_approvals import (  # noqa: E402
    REDUNDANT_CD_COMMAND, REDUNDANT_CD_SEGMENT, REDUNDANT_GIT_C, ROOT, allowed, classify, head_command,
    is_readonly, split_segments,
)

REPO = str(ROOT).replace("\\", "/")   # 슬래시형 경로
REPO_BS = str(ROOT)                    # 역슬래시형 경로 (Windows 원형)


# ── ① 쪼개기: 따옴표·heredoc·$() 안은 건드리지 않는다 ────────────────────────

def test_최상위_연산자에서만_쪼갠다():
    assert split_segments("a && b | c ; d") == ["a", "b", "c", "d"]


def test_따옴표_안의_파이프는_쪼개지_않는다():
    """이걸 놓치면 `python -c` 한 줄이 조각 여러 개로 잡혀 원인 건수가 부풀려진다."""
    assert split_segments("""python -c "print(a | b)" """) == ['python -c "print(a | b)"']
    assert split_segments("grep -E 'a|b' x") == ["grep -E 'a|b' x"]


def test_명령치환_안은_쪼개지_않는다():
    got = split_segments("n=$(git show x | grep -c y || echo 0)")
    assert got == ["n=$(git show x | grep -c y || echo 0)"]


def test_heredoc_본문은_보지_않는다():
    """본문은 셸 명령이 아니라 파이썬 소스다 — 세면 없는 명령을 세게 된다."""
    assert split_segments("python - <<'PY'\nprint(1 | 2)\nPY") == ["python -"]


def test_빈_조각은_버린다():
    assert split_segments("a &&  && b") == ["a", "b"]


# ── ② cd 패턴: 명령용과 조각용이 다르다 ─────────────────────────────────────

REPO_MSYS = "/" + REPO[0].lower() + REPO[2:]   # Git Bash(MSYS) 경로: C:/Users/… → /c/Users/…


@pytest.mark.parametrize("command", [
    f"cd {REPO} && python -m pytest tests/",
    f'cd "{REPO}" && git status',
    f"cd {REPO}/ && ls",
    f"cd {REPO}; ls",
    f'cd "{REPO_BS}" && git status',
    # ★ vanasso.kr 2026-09-06 실측 — 10세션 cd 접두사 1,000건 중 168건이 아래 세 변형으로 훅을 통과했다
    f"cd {REPO} 2>/dev/null; ls",           # 리다이렉트를 끼운 형태 (49건)
    f"cd {REPO} ls",                        # 구분자 없음 (99건) — bash는 "too many arguments"로 죽고 ls는 안 돈다
    f"cd {REPO_MSYS} && ls",                # MSYS 경로 (20건)
    f"cd {REPO_MSYS} 2>/dev/null && ls",
])
def test_명령_전체에서_불필요한_cd를_잡는다(command):
    assert REDUNDANT_CD_COMMAND.match(command)


@pytest.mark.parametrize("command", [
    "cd app && flutter test",       # 하위 디렉토리는 대상이 아니다
    "cd app",
    "cd ..",
    'cd "C:/Users/user/AppData/Local/Temp/somewhere-else" && ls',
    "python scripts/build_db.py",
    f"cd {REPO}/scripts && ls",     # 루트 아래 하위로 가는 cd — 대상이 아니다
    f"cd {REPO}-other && ls",       # 이름이 루트로 시작하는 다른 디렉토리
    f"cd {REPO}",                   # 단독 cd — 명령용 패턴은 잡으면 안 된다(조각용이 잡는다)
    f"cd {REPO_MSYS}",
])
def test_정당한_cd와_일반_명령은_안_잡는다(command):
    assert not REDUNDANT_CD_COMMAND.match(command)


@pytest.mark.parametrize("command", [
    # bid-collectors 2026-09-26 실측 311초·25초 — `git add *` 규칙이 있는데 -C 때문에 물었다
    f"git -C {REPO_MSYS} add CLAUDE.md docs/CONTRACT.md",
    f"git -C {REPO} add docs/handover/v1.4.0.md && git -C {REPO} commit -q -F .commit_msg.txt",
    f'git -C "{REPO_BS}" commit -F .commit_msg.txt',
    f"git -C {REPO}/ mv a b",
])
def test_루트를_가리키는_쓰기_git_C를_잡는다(command):
    assert REDUNDANT_GIT_C.match(command)


@pytest.mark.parametrize("command", [
    "git add docs/x.md",                          # 권장 형태
    f"git -C {REPO} status --short",              # 읽는 git — cwd 드리프트 확인용으로 둔다([H13])
    f"git -C {REPO_MSYS} diff docs/handover/v1.4.0.md",
    f"git -C {REPO} addendum",                    # add로 시작하는 다른 낱말
    f"git -C {REPO}/scripts status",              # 루트 아래 하위 — 대상이 아니다
    f"git -C {REPO}-other status",                # 이름이 루트로 시작하는 다른 저장소
    "git -C C:/Users/user/Documents/bidwatch log --oneline -1",   # 다른 저장소
    f"echo git -C {REPO} add x",                  # git이 명령 머리가 아니다
])
def test_정당한_git_C와_일반_명령은_안_잡는다(command):
    assert not REDUNDANT_GIT_C.match(command)


def test_조각용_패턴도_리다이렉트_변형과_MSYS_경로를_잡는다():
    """분류(measure_approvals)는 조각 단위로 본다 — 명령용만 넓히면 훅은 막는데 보고서는 '형태 0건'이 된다."""
    assert REDUNDANT_CD_SEGMENT.match(f"cd {REPO} 2>/dev/null")
    assert REDUNDANT_CD_SEGMENT.match(f"cd {REPO_MSYS}")
    assert not REDUNDANT_CD_SEGMENT.match(f"cd {REPO}/scripts")


def test_조각용_패턴은_연산자가_없는_형태를_잡는다():
    """★ 여기가 거짓 0건을 냈던 자리다. 쪼갠 뒤에는 `&&`가 사라져 있다."""
    seg = split_segments(f"cd {REPO} && ls")[0]
    assert seg == f"cd {REPO}"
    assert REDUNDANT_CD_SEGMENT.match(seg), "조각용 패턴이 안 맞으면 cd 원인이 0건으로 보고된다"
    assert not REDUNDANT_CD_COMMAND.match(seg), "명령용 패턴은 조각에 맞으면 안 된다"


# ── ③ 읽기 전용 판정: 모르면 '아니다' 쪽으로 ────────────────────────────────

@pytest.mark.parametrize("segment", [
    "grep -n foo bar.txt", "head -5 x", "wc -l x", "echo hi", "sort x", "cat x",
])
def test_읽기_전용을_알아본다(segment):
    assert is_readonly(segment)


@pytest.mark.parametrize("segment", [
    "sed -i 's/a/b/' x",              # 파일을 고친다
    "sort -o out.txt in.txt",         # 파일을 쓴다
    "grep foo x > out.txt",           # 리다이렉트로 파일을 만든다
    "rm x", "tar -xf x.zip", "pip install foo",
])
def test_상태를_바꾸면_읽기_전용이_아니다(segment):
    assert not is_readonly(segment)


def test_모르는_명령은_읽기_전용으로_치지_않는다():
    """자동 제안은 **안전한 쪽으로만** 기울어야 한다 — 모르면 열지 않는다."""
    assert not is_readonly("mysterious-tool --do-something")


def test_리다이렉트와_fd복제를_구분한다():
    """`2>&1`은 파일을 만들지 않는다. 이걸 못 가리면 멀쩡한 파이프가 전부 상태 변경이 된다."""
    assert is_readonly("grep foo x 2>&1")


# ── ④ 분류: 처방이 갈리는 지점 ──────────────────────────────────────────────

@pytest.mark.parametrize(("segment", "kind"), [
    (f"cd {REPO}", "형태"),
    ("for f in a b", "셸제어문"),
    ("done", "셸제어문"),
    ("grep -n x y", "읽기전용"),
    ("rm x", "상태변경"),
    ("flutter test", "판단필요"),
])
def test_원인을_갈라_분류한다(segment, kind):
    assert classify(segment) == kind


# ── ⑤ 규칙 매칭: 접두사 의미 ────────────────────────────────────────────────

def test_별표_규칙은_인자_유무를_모두_받는다():
    assert allowed("git status", ["git status *"])
    assert allowed("git status --short", ["git status *"])


def test_별표_없는_규칙은_정확일치다():
    """`git commit -F .commit_msg.txt`에 `2>&1`을 붙였다는 이유로 매번 물었던 실사례."""
    assert allowed("git commit -F .commit_msg.txt", ["git commit -F .commit_msg.txt"])
    assert not allowed("git commit -F .commit_msg.txt 2>&1", ["git commit -F .commit_msg.txt"])
    assert allowed("git commit -F .commit_msg.txt 2>&1", ["git commit -F *"])


def test_공백_없는_꼬리_별표도_받는다():
    """★ 2026-08-22 실사례: 규칙을 `X`+`X *` 두 줄에서 `X*` 한 줄로 합치자 분석기가
    그 규칙을 **통째로 못 보고** `git commit` 13건을 '물었을 것'으로 세었다(실제로는 안 물었다).

    규칙 문법은 하네스와 `allowed()` 두 곳에 구현돼 있다 — 조용히 어긋나는 자리라 대조한다.
    """
    rules = ["python scripts/measure_wait.py*"]
    assert allowed("python scripts/measure_wait.py", rules)          # 맨몸
    assert allowed("python scripts/measure_wait.py --grep x", rules)  # 공백 뒤 인자
    assert allowed("python scripts/measure_wait.py--grep=x", rules)   # ★ 공백 없이 붙는 형태
    # 넓히기만 하면 안 된다 — 다른 스크립트까지 덮으면 규칙이 거짓말이 된다
    assert not allowed("python scripts/measure_approvals.py", rules)


def test_공백_있는_별표는_공백_없는_형태를_안_덮는다():
    """`X *`와 `X*`의 의미 차이를 굳힌다. 이걸 뭉개면 규칙이 조용히 넓어진다."""
    assert not allowed("python scripts/measure_wait.py--grep=x",
                       ["python scripts/measure_wait.py *"])


def test_콜론_별표는_공백_별표와_같다():
    """★ vanasso.kr 2026-09-06 실사례: local의 규칙 97건 대부분이 `Bash(node:*)`·`Bash(git add:*)`처럼
    **콜론+별표** 형태였는데 분석기가 이 문법을 몰라 정확 일치로 취급 → "51건 중 51건 승인 필요(100%)"라는
    거짓 보고서. 공식 문서: "`:*` 접미사는 꼬리 ` *`와 동등하다 — `Bash(ls:*)`는 `Bash(ls *)`와 같은
    명령을 매칭한다"(code.claude.com/docs/en/permissions, 패턴 끝에서만 인식)."""
    assert allowed("node script.js", ["node:*"])
    assert allowed("node", ["node:*"])                  # 맨몸도 덮는다 (` *`와 동등)
    assert allowed("git add -A", ["git add:*"])
    assert allowed("git add", ["git add:*"])
    assert not allowed("nodemon x", ["node:*"])         # 공백 경계 — `node *`와 같은 의미
    assert not allowed("git addx", ["git add:*"])


def test_콜론_별표는_끝에서만_와일드카드다():
    """공식 문서: `Bash(git:* push)`의 콜론은 리터럴이다 — 가운데 `:*`를 와일드카드로 풀면 규칙이 넓어진다."""
    assert not allowed("git commit push", ["git:* push"])
    assert allowed("git:* push", ["git:* push"])        # 리터럴 정확 일치


def test_경로가_붙은_실행파일은_이름만_본다():
    assert head_command("C:/tools/adb.exe devices") == "adb"
    assert head_command("python scripts/x.py") == "python"
