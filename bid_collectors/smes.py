"""중소벤처기업부 사업공고 수집기.

API: http://apis.data.go.kr/1421000/mssBizService_v2/getbizList_v2
인증: serviceKey (DATA_GO_KR_KEY)
응답: XML
"""

import logging
import time
from datetime import datetime, timedelta

from lxml import etree

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "http://apis.data.go.kr/1421000/mssBizService_v2/getbizList_v2"
DEFAULT_NUM_OF_ROWS = 100


class SmesCollector(BaseCollector):
    """중소벤처기업부 사업공고 수집기."""

    source_name = "중소벤처기업부"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        end_date = datetime.now()
        start_date = (end_date - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        notices: list[Notice] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)

        async with create_client(timeout=30.0) as client:
            page = 1
            while page <= max_pages:
                params = {
                    "serviceKey": self.api_key,
                    "pageNo": str(page),
                    "numOfRows": str(DEFAULT_NUM_OF_ROWS),
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d"),
                }

                try:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                except Exception as e:
                    logger.error(f"[중소벤처기업부] 페이지 {page} 요청 실패: {e}")
                    break

                try:
                    items, total_count = _parse_xml_response(resp.content)
                except ValueError as e:
                    logger.error(f"[중소벤처기업부] XML 파싱 에러: {e}")
                    break

                if not items:
                    break

                pages_processed += 1

                for item in items:
                    try:
                        notice = _item_to_notice(item)
                        notices.append(notice)
                    except Exception as e:
                        logger.warning(f"[중소벤처기업부] 항목 파싱 실패: {e}")

                if page * DEFAULT_NUM_OF_ROWS >= total_count:
                    break
                page += 1

        return notices, pages_processed

    async def health_check(self) -> dict:
        start = time.time()
        try:
            now = datetime.now()
            async with create_client(timeout=10.0) as client:
                params = {
                    "serviceKey": self.api_key,
                    "pageNo": "1",
                    "numOfRows": "1",
                    "startDate": (now - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "endDate": now.strftime("%Y-%m-%d"),
                }
                resp = await client.get(API_URL, params=params)
                resp.raise_for_status()
                _parse_xml_response(resp.content)
                ms = int((time.time() - start) * 1000)
                return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}


def _parse_xml_response(xml_bytes: bytes) -> tuple[list[etree._Element], int]:
    """XML 응답에서 item 목록과 totalCount를 추출."""
    root = etree.fromstring(xml_bytes)

    result_code = root.findtext(".//resultCode")
    if result_code and result_code != "00":
        msg = root.findtext(".//resultMsg") or "Unknown error"
        raise ValueError(f"API 에러: {result_code} - {msg}")

    total = int(root.findtext(".//totalCount") or "0")
    items = root.findall(".//item")
    return items, total


def _item_to_notice(item: etree._Element) -> Notice:
    """XML item 요소를 Notice 모델로 변환."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    item_id = t("itemId")
    title = clean_html_to_text(t("title"))
    content = clean_html_to_text(t("dataContents"))

    app_start = t("applicationStartDate")
    app_end = t("applicationEndDate")
    start_str = parse_date(app_start)
    end_str = parse_date(app_end)
    status = determine_status(end_str) if end_str else "ongoing"

    view_url = t("viewUrl")

    # 예산/규모 파싱
    budget_raw = t("suptScale") or t("supt_scale")
    budget = None
    if budget_raw:
        import re
        nums = re.findall(r"[\d,]+", budget_raw)
        if nums:
            try:
                budget = int(nums[0].replace(",", ""))
            except ValueError:
                pass

    # 첨부파일
    attachments = _extract_attachments(item)

    return Notice(
        source="중소벤처기업부",
        bid_no=f"MSS-{item_id}",
        title=title,
        organization="중소벤처기업부",
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=view_url,
        detail_url=view_url,
        content=content[:500] if content else "",
        budget=budget,
        category=t("writerPosition"),
        attachments=attachments,
        extra={
            k: v for k, v in {
                "budget_raw": budget_raw,
                "writer": t("writer"),
            }.items() if v is not None and v != ""
        } or None,
    )


def _extract_attachments(item: etree._Element) -> list[dict] | None:
    """XML item에서 fileName/fileUrl 쌍을 추출."""
    names = [el.text for el in item.findall("fileName") if el.text]
    urls = [el.text for el in item.findall("fileUrl") if el.text]
    if not urls:
        return None
    attachments = []
    for i, url in enumerate(urls):
        name = names[i] if i < len(names) else f"첨부파일{i + 1}"
        attachments.append({"name": name, "url": url})
    return attachments or None
