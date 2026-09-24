"""수집기 공통 베이스 클래스."""

import os
import re
import time
import logging
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime
from urllib.parse import quote

from lxml import etree
from pydantic import ValidationError

from .models import Notice, CollectResult

logger = logging.getLogger("bid_collectors")

# httpx 예외 문자열에는 쿼리스트링까지 든 전체 URL이 실린다 — 키 파라미터 값을 지운다
_KEY_PARAM_RE = re.compile(r"((?:serviceKey|ServiceKey|crtfcKey)=)[^&\s'\"]+")


def mask_secret(text: str, secret: str | None = None) -> str:
    """오류 문자열에서 API 키를 가린다(쿼리 파라미터 값 + 원문·URL 인코딩된 키)."""
    text = _KEY_PARAM_RE.sub(r"\1***", text)
    if secret:
        for form in {secret, quote(secret, safe=""), quote(secret)}:
            text = text.replace(form, "***")
    return text


class MissingFieldError(ValueError):
    """필수 필드가 없는 항목 — 건너뛰고 사유를 errors로 보고한다."""


def require_fields(**fields) -> None:
    """필수 필드가 None·빈 문자열(공백만 포함)이면 MissingFieldError.

    숫자 0은 유효한 값이다 — `not value`로 보면 ID 0을 빠진 값으로 오판한다(CLAUDE.md '숫자 필드에 or 금지').
    ID가 빠진 항목을 통과시키면 `{접두사}-`로 합쳐져 서로 다른 공고가 BidWatch upsert에서 한 행을 덮어쓴다.
    """
    missing = [name for name, value in fields.items()
               if value is None or (isinstance(value, str) and not value.strip())]
    if missing:
        raise MissingFieldError("필수 필드 없음: " + ",".join(missing))


def _skip_reason(e: Exception) -> str:
    """건너뛴 사유 — 같은 원인이 한 줄로 모이도록 값이 아니라 종류·필드 이름으로 만든다."""
    if isinstance(e, MissingFieldError):
        return str(e)
    if isinstance(e, ValidationError):
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
        return f"ValidationError: {','.join(fields)}"
    return type(e).__name__


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

    def _mask(self, text: str) -> str:
        return mask_secret(text, self.api_key)

    def _record_skip(self, skips: Counter, e: Exception, item) -> None:
        """항목 하나의 변환 실패를 센다 — 그 항목만 건너뛰고 수집은 계속한다(나머지 결과를 지우지 않는다).

        합계는 _skip_message()로 errors에 싣는다. 로그만 남기면 소비자는 누락을 모른다.
        """
        reason = _skip_reason(e)
        skips[reason] += 1
        # XML 항목은 repr이 "<Element item at 0x…>"뿐이라 원문으로 남긴다
        raw = etree.tostring(item, encoding="unicode") if isinstance(item, etree._Element) else repr(item)
        logger.warning(f"[{self.source_name}] 항목 건너뜀: {reason} — {self._mask(raw)[:200]}",
                       exc_info=not isinstance(e, MissingFieldError))

    @staticmethod
    def _skip_message(skips: Counter) -> str | None:
        """건너뛴 항목의 사유별 건수 한 줄. 없으면 None."""
        if not skips:
            return None
        reasons = ", ".join(f"{r} {n}건" for r, n in skips.most_common())
        return f"항목 파싱 예외로 {sum(skips.values())}건 건너뜀 — {reasons} (응답 형식 변경 의심)"

    @abstractmethod
    async def _fetch(
        self, days: int = 1, **kwargs
    ) -> tuple[list[Notice], int] | tuple[list[Notice], int, list[str]]:
        """공고 수집 — 서브클래스가 구현.

        Returns:
            (notices 리스트, 처리한 페이지 수[, 부분 실패·절단 메시지])
            세 번째 요소가 비어 있지 않으면 collect()가 is_partial=True로 보고한다.
        """
        ...

    async def collect(self, days: int = 1, **kwargs) -> CollectResult:
        """공고 수집 메인 메서드. _fetch()를 호출하고 결과를 CollectResult로 래핑."""
        start = time.time()
        errors: list[str] = []
        notices: list[Notice] = []
        pages_processed = 0

        try:
            result = await self._fetch(days=days, **kwargs)
            notices, pages_processed = result[0], result[1]
            if len(result) > 2:
                errors.extend(result[2])
        except Exception as e:
            logger.error(f"[{self.source_name}] 수집 실패: {self._mask(str(e))}")
            errors.append(str(e))

        # 수집기가 이미 가렸어도 한 번 더 — errors는 소비자 DB까지 간다
        errors = [self._mask(msg) for msg in errors]
        is_partial = bool(errors)

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
