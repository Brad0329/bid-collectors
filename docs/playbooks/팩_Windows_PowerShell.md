# 팩: Windows / PowerShell (Windows에서 개발할 때)

> 선택형 팩 — 이 프로젝트에 해당 없으면 이 파일을 삭제한다.
> 출처: hanjadic 프로젝트 실측(2026-08). Windows PowerShell 5.1 기준.

## 인코딩

- **임시 `.ps1`은 주석까지 ASCII로 쓴다.** AI 도구의 Write는 BOM 없는 UTF-8로 쓰는데
  Windows PowerShell 5.1은 `.ps1`을 ANSI로 읽어 한글이 깨지고 **파서가 엉뚱한 줄에서
  `Unexpected token`으로 죽는다.** 에러 메시지로는 원인(인코딩)을 짚을 수 없다.
  한글이 필요한 스크립트는 파이썬으로 쓰거나 BOM을 붙인다.
- **콘솔 기본 코덱(cp949)은 확장 유니코드에서 출력을 통째로 깨뜨린다**(UnicodeEncodeError).
  Python이면 stdout을 utf-8로 reconfigure, 장기 실행 프로세스면 `PYTHONIOENCODING=utf-8`.

## PowerShell 함정

- **여러 줄 문자열을 네이티브 exe(git 등)에 인라인/here-string으로 넘기면 인용이 깨진다.**
  커밋 메시지는 파일에 쓰고 `git commit -F <파일>`로. (`pathspec ... did not match` 오류 2회.)
- **PowerShell로 여러 줄 텍스트 치환을 하지 않는다.** `` `n ``이 문자열에 그대로 박히고,
  CRLF 파일에 LF 패턴이 조용히 안 맞는다(같은 세션에서 두 번 실패). 여러 줄 수정은 편집 도구로.
- **빈 문자열 인자를 삼킨다.** `명령 -m ""` 형태가 인자 누락으로 죽는다 — 논리식 등으로 우회.
- **`pkill -f`는 Windows 프로세스를 못 죽인다.** 프로세스 정리는 CIM + `Stop-Process`로.
  (살아남은 옛 서버가 포트를 쥐고 옛 설정으로 응답해 코드 문제로 오진한 실사례.)
- **UI 자동화**: `Add-Type -PassThru`는 타입 배열을 반환해 P/Invoke가 깨지고, SendKeys는
  한글 입력이 불가하다(클립보드+Ctrl+V로 우회).

## Windows 일반

- **Python은 네이티브 Windows 빌드를 쓴다.** MSYS2 mingw 빌드(`python3`)는 venv 레이아웃이
  달라(`bin/` vs `Scripts/`) 네이티브 도구와 안 섞인다.
- **열려 있는 파일은 덮어쓸 수 없다(WinError 32).** 테스트가 DB를 연 채로 재빌드하면 실패 —
  재빌드는 `--output`으로 다른 경로에 쓴다. 서버 가동 중 재빌드도 같은 함정.
- **Git Bash의 `/tmp`·경로 자동 변환이 네이티브 도구와 충돌한다.** 네이티브 프로그램은 MSYS
  가상 경로를 못 읽고, `/sdcard/...` 같은 인자가 `C:/Program Files/Git/sdcard/...`로 변환된다
  (adb에서 실제 발생) — `MSYS_NO_PATHCONV=1`. docker 등 리눅스 경로 인자를 받는 도구에서
  반복될 함정이다.
- **작업 디렉토리 유지를 100% 믿지 않는다.** relative 명령이 "찾을 수 없다"류로 죽으면 cwd부터
  의심한다 — 증상이 원인을 안 가린다(경로를 못 찾자 `"The module '.venv' could not be loaded"`
  같은 엉뚱한 해석이 나온 실사례).

## Claude Code 허용 규칙 (Windows 특화 요점)

상세는 `노하우_승인_대기_최소화.md`. Windows에서 특히:
- 기본 셸 도구가 PowerShell이면 **`PowerShell(...)` 규칙**이 필요하다 — `Bash(...)` 규칙은 안 걸린다.
- 공백 없는 경로를 따옴표로 감싸는 Bash 습관이 규칙 매칭을 깨뜨린다.
