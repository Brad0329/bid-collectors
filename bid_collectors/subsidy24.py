"""보조금24 공공서비스(혜택) 정보 수집기.

API: https://api.odcloud.kr/api/gov24/v3/serviceList
Swagger: https://infuser.odcloud.kr/api/stages/44436/api-docs
인증: serviceKey (DATA_GO_KR_KEY) — data.go.kr에서 15113968 서비스 활용 신청 필요
"""

import logging
import time
from datetime import datetime, timedelta

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import clean_html_to_text

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

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        only_business = kwargs.get("only_business", False)
        cutoff = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        notices: list[Notice] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)

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
                    logger.error(f"[보조금24] 페이지 {page} 요청 실패: {e}")
                    break

                if "code" in data and data["code"] < 0:
                    logger.error(f"[보조금24] API 에러: {data.get('msg', '')}")
                    break

                items = data.get("data", [])
                if not items:
                    break

                pages_processed += 1
                total_count = data.get("matchCount", 0)

                for item in items:
                    notice = _item_to_notice(item)
                    if notice is None:
                        continue
                    if only_business and not _is_business_target(item):
                        continue
                    notices.append(notice)

                if page * DEFAULT_PER_PAGE >= total_count:
                    break
                page += 1

        return notices, pages_processed

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
            return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}


def _item_to_notice(item: dict) -> Notice | None:
    """API 응답 항목을 Notice 모델로 변환."""
    service_id = item.get("서비스ID", "")
    title = item.get("서비스명", "")
    if not service_id or not title:
        return None

    deadline = item.get("신청기한", "")
    end_str = parse_date(deadline)

    # 상세조회URL이 있으면 사용, 없으면 보조금24 기본 URL
    detail_url = item.get("상세조회URL", "")
    url = detail_url or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{service_id}"

    content_parts = []
    if item.get("서비스목적요약"):
        content_parts.append(item["서비스목적요약"])
    if item.get("지원내용"):
        content_parts.append(clean_html_to_text(item["지원내용"]))
    content = "\n".join(content_parts)

    return Notice(
        source="보조금24",
        bid_no=f"GOV24-{service_id}",
        title=title,
        organization=item.get("소관기관명", ""),
        start_date=None,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=detail_url,
        content=content,
        category=item.get("서비스분야", ""),
        extra={
            k: v for k, v in {
                "support_type": item.get("지원유형", ""),
                "target": item.get("지원대상", ""),
                "selection_criteria": item.get("선정기준", ""),
                "apply_method": item.get("신청방법", ""),
                "deadline_raw": deadline,
                "department": item.get("부서명", ""),
                "agency_type": item.get("소관기관유형", ""),
                "user_type": item.get("사용자구분", ""),
                "reception_agency": item.get("접수기관", ""),
                "phone": item.get("전화문의", ""),
                "view_count": item.get("조회수"),
            }.items() if v is not None and v != ""
        } or None,
    )


def _is_business_target(item: dict) -> bool:
    """기업 대상 서비스인지 판별."""
    check_fields = [
        item.get("서비스명", ""),
        item.get("지원대상", ""),
        item.get("사용자구분", ""),
        item.get("서비스분야", ""),
    ]
    text = " ".join(check_fields)
    return any(kw in text for kw in BUSINESS_KEYWORDS)
