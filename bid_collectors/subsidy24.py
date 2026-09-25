"""보조금24 공공서비스(혜택) 정보 수집기.

API: https://api.odcloud.kr/api/gov24/v3/serviceList
Swagger: https://infuser.odcloud.kr/api/stages/44436/api-docs
인증: serviceKey (DATA_GO_KR_KEY) — data.go.kr에서 15113968 서비스 활용 신청 필요
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import as_text, clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "https://api.odcloud.kr/api/gov24/v3/serviceList"
DEFAULT_PER_PAGE = 100

# 기업 대상 키워드 (시민 복지 항목 제외용)
BUSINESS_KEYWORDS = [
    "기업", "사업자", "소상공인", "창업", "중소", "벤처",
    "스타트업", "법인", "자영업", "중견", "수출",
]


class Subsidy24Collector(BaseCollector):
    """보조금24 공공서비스(혜택) 정보 수집기."""

    source_name = "보조금24"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        only_business = kwargs.get("only_business", False)
        cutoff = (datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # 수정일시 값은 "YYYYMMDDHHMMSS" 문자열 비교다 — "YYYY-MM-DD ..."로 보내면 '0' > '-'라 거의 전부가 걸린다
        # (2026-09-23 실측: 1일치가 10,498건/전체 10,933건 → 이 형식으로 24건)
        cutoff_str = cutoff.strftime("%Y%m%d%H%M%S")

        notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)
        total_count = 0
        skips: Counter[str] = Counter()

        async with create_client(timeout=30.0) as client:
            page = 1
            while page <= max_pages:
                params = {
                    "serviceKey": self.api_key,
                    "page": str(page),
                    "perPage": str(DEFAULT_PER_PAGE),
                    "cond[수정일시::GTE]": cutoff_str,
                }

                try:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    msg = self._mask(f"페이지 {page} 요청 실패: {type(e).__name__}: {e}")
                    logger.error(f"[보조금24] {msg}")
                    errors.append(msg)
                    break

                if "code" in data and data["code"] < 0:
                    msg = self._mask(f"페이지 {page} API 에러: {data['code']} - {data.get('msg', '')}")
                    logger.error(f"[보조금24] {msg}")
                    errors.append(msg)
                    break

                items = data.get("data", [])
                if not items:
                    break

                pages_processed += 1
                total_count = data.get("matchCount", 0)

                for item in items:
                    try:
                        notice = _item_to_notice(item)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
                    if only_business and not _is_business_target(item):
                        continue
                    notices.append(notice)

                if page * DEFAULT_PER_PAGE >= total_count:
                    break
                page += 1
            else:
                msg = (
                    f"max_pages={max_pages} 상한 도달로 중단 — 전체 {total_count}건 중 "
                    f"{max_pages * DEFAULT_PER_PAGE}건까지만 조회"
                )
                logger.warning(f"[보조금24] {msg}")
                errors.append(msg)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages_processed, errors

    async def health_check(self) -> dict:
        start = time.time()
        try:
            async with create_client(timeout=10.0) as client:
                params = {
                    "serviceKey": self.api_key,
                    "page": "1",
                    "perPage": "1",
                }
                resp = await client.get(API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                if "code" in data and data["code"] < 0:
                    raise ValueError(data.get("msg", "API 에러"))
                ms = int((time.time() - start) * 1000)
                return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)), "response_time_ms": ms}


def _item_to_notice(item: dict) -> Notice:
    """API 응답 항목을 Notice 모델로 변환. 필수 필드가 없으면 MissingFieldError."""
    service_id = item.get("서비스ID")
    title = item.get("서비스명")
    require_fields(서비스ID=service_id, 서비스명=title)

    # 선택 필드는 null·타입 이상이어도 항목을 버리지 않는다(v1.2.5 B) — 원문은 extra에 그대로 남는다
    deadline = as_text(item.get("신청기한"))
    end_str = parse_date(deadline)

    # 상세조회URL이 있으면 사용, 없으면 보조금24 기본 URL
    detail_url = as_text(item.get("상세조회URL"))
    url = detail_url or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{service_id}"

    content_parts = []
    if summary := as_text(item.get("서비스목적요약")):
        content_parts.append(summary)
    if detail := as_text(item.get("지원내용")):
        content_parts.append(clean_html_to_text(detail))
    content = "\n".join(content_parts)

    return Notice(
        source="보조금24",
        bid_no=f"GOV24-{service_id}",
        title=title,
        organization=as_text(item.get("소관기관명")),
        start_date=None,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=detail_url,
        content=content,
        category=as_text(item.get("서비스분야")),
        extra=raw_fields(item),  # 원문 전부(v1.2.5, 원칙 ①) — 한국어 필드명 그대로
    )


def _is_business_target(item: dict) -> bool:
    """기업 대상 서비스인지 판별.

    어떤 값(null·숫자·list)에도 예외를 던지지 않는다 — 항목 변환 try 밖에서 불려, 여기서 예외가 나면
    앞 페이지까지 결과 전체를 잃는다(v1.2.5 B).
    """
    text = " ".join(as_text(item.get(k)) for k in ("서비스명", "지원대상", "사용자구분", "서비스분야"))
    return any(kw in text for kw in BUSINESS_KEYWORDS)
