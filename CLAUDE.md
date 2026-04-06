# bid-collectors 작업 규칙

## Agent 역할 분담

- **메인 agent**: 코드 개발에 집중
- **테스트 agent (별도, foreground)**: 수집기 1개 완성 시 단위 테스트, Phase 완료 시 통합 테스트. 테스트 완료 확인 후 다음 작업 진행.
- **서류 agent (별도)**: Phase 완료 시 `docs/work_log/`에 작업 로그 작성

## 작업 로그 규칙

- 위치: `docs/work_log/`
- Phase 완료 시 별도 agent를 띄워 작성
- 포함 내용: 수행한 작업, 핵심 로직 (재현 가능한 수준), 주요 실수, 향후 주의점
- 새 세션에서 새 Phase 시작 시 이전 로그를 읽고 작업하므로 일관성 유지에 핵심

## 테스트 규칙

- 수집기 1개 완성 → 별도 테스트 agent가 단위 테스트
- Phase 완료 → 별도 테스트 agent가 통합 테스트
- 순차(foreground) 실행 — 테스트 통과 확인 후 다음 작업 진행
