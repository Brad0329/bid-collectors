"""중소벤처기업부 사업공고 수집기.

API: http://apis.data.go.kr/1421000/mssBizService_v2/getbizList_v2
인증: serviceKey (DATA_GO_KR_KEY)
응답: XML
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from lxml import etree

from .base import BaseCollector, raw_fields, require_fields
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

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        end_date = datetime.now()
        start_date = (end_date - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
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
                    "pageNo": str(page),
                    "numOfRows": str(DEFAULT_NUM_OF_ROWS),
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d"),
                }

                try:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                except Exception as e:
                    msg = self._mask(f"페이지 {page} 요청 실패: {type(e).__name__}: {e}")
                    logger.error(f"[중소벤처기업부] {msg}")
                    errors.append(msg)
                    break

                try:
                    items, total_count = _parse_xml_response(resp.content)
                except (ValueError, etree.XMLSyntaxError) as e:
                    msg = self._mask(f"페이지 {page} 응답 오류: {e}")
                    logger.error(f"[중소벤처기업부] {msg}")
                    errors.append(msg)
                    break

                if not items:
                    break

                pages_processed += 1

                for item in items:
                    try:
                        notice = _item_to_notice(item)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
                    notices.append(notice)

                if page * DEFAULT_NUM_OF_ROWS >= total_count:
                    break
                page += 1
            else:
                msg = (
                    f"max_pages={max_pages} 상한 도달로 중단 — 전체 {total_count}건 중 "
                    f"{max_pages * DEFAULT_NUM_OF_ROWS}건까지만 조회"
                )
                logger.warning(f"[중소벤처기업부] {msg}")
                errors.append(msg)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages_processed, errors

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
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)), "response_time_ms": ms}


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
    require_fields(itemId=item_id, title=title)
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
        extra=raw_fields(item),  # 원문 전부(v1.2.5, 원칙 ①) — 반복 태그 fileName·fileUrl은 list
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
