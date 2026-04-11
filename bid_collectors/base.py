"""수집기 공통 베이스 클래스."""

import os
import time
import logging
from abc import ABC, abstractmethod
from datetime import datetime

from .models import Notice, CollectResult

logger = logging.getLogger("bid_collectors")


class BaseCollector(ABC):
    source_name: str = ""

    def __init__(self, api_key: str | None = None, **kwargs):
        self.api_key = api_key or os.environ.get(self._env_key())
        if not self.api_key:
            raise ValueError(
                f"{self.source_name}: API 키가 필요합니다. "
                f"생성자에 api_key를 전달하거나 환경변수 {self._env_key()}를 설정하세요."
            )

    def _env_key(self) -> str:
        """환경변수명. 서브클래스에서 오버라이드 가능."""
        return "DATA_GO_KR_KEY"

    @abstractmethod
    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        """공고 수집 — 서브클래스가 구현.

        Returns:
            (notices 리스트, 처리한 페이지 수)
        """
        ...

    async def collect(self, days: int = 1, **kwargs) -> CollectResult:
        """공고 수집 메인 메서드. _fetch()를 호출하고 결과를 CollectResult로 래핑."""
        start = time.time()
        errors: list[str] = []
        notices: list[Notice] = []
        pages_processed = 0
        is_partial = False

        try:
            notices, pages_processed = await self._fetch(days=days, **kwargs)
        except Exception as e:
            logger.error(f"[{self.source_name}] 수집 실패: {e}")
            errors.append(str(e))
            is_partial = True

        duration = time.time() - start

        # bid_no 기준 중복 제거
        seen = set()
        deduped = []
        for n in notices:
            key = (n.source, n.bid_no)
            if key not in seen:
                seen.add(key)
                deduped.append(n)

        return CollectResult(
            notices=deduped,
            source=self.source_name,
            collected_at=datetime.now(),
            duration_seconds=round(duration, 2),
            total_fetched=len(notices),
            total_after_dedup=len(deduped),
            pages_processed=pages_processed,
            errors=errors,
            is_partial=is_partial,
        )

    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회. 지원하지 않는 수집기는 None 반환."""
        return None

    async def health_check(self) -> dict:
        """API 연결 상태 확인. 서브클래스에서 오버라이드 권장."""
        return {"status": "ok", "source": self.source_name, "message": "not implemented"}
