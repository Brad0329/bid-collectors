"""나라장터 입찰공고 수집기.

API: https://apis.data.go.kr/1230000/ad/BidPublicInfoService04
서비스 3개: 용역(ServcPPSSrch), 물품(ThngPPSSrch), 공사(CnstwkPPSSrch)
응답: XML, 100건/페이지
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from lxml import etree

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

logger = logging.getLogger("bid_collectors")

BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# 서비스별 operation
BID_SERVICES = {
    "용역": "getBidPblancListInfoServcPPSSrch",
    "물품": "getBidPblancListInfoThngPPSSrch",
    "공사": "getBidPblancListInfoCnstwkPPSSrch",
}

ROWS_PER_PAGE = 100
DATE_CHUNK_DAYS = 7
RETRY_WAIT = 30
MAX_RETRIES = 3


def _split_date_range(days: int, chunk: int = DATE_CHUNK_DAYS) -> list[tuple[str, str]]:
    """날짜 범위를 chunk일 단위로 분할. 각 요소는 (시작, 종료) yyyyMMddHHmm 형식."""
    end = datetime.now()
    start = end - timedelta(days=days)
    ranges = []
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(days=chunk), end)
        ranges.append((
            cur.strftime("%Y%m%d") + "0000",
            chunk_end.strftime("%Y%m%d") + "2359",
        ))
        cur = chunk_end
    return ranges


def _parse_xml_items(xml_bytes: bytes) -> tuple[list[etree._Element], int]:
    """XML 응답에서 item 목록과 totalCount를 추출."""
    root = etree.fromstring(xml_bytes)

    # 에러 응답 체크
    result_code = root.findtext(".//resultCode")
    if result_code and result_code != "00":
        msg = root.findtext(".//resultMsg") or "Unknown error"
        raise ValueError(f"API 에러: {result_code} - {msg}")

    total = int(root.findtext(".//totalCount") or "0")
    items = root.findall(".//item")
    return items, total


def _item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """XML item 요소를 Notice 모델로 변환."""

    def t(tag: str) -> str:
        """태그 텍스트 추출. 없으면 빈 문자열."""
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    bid_no_raw = t("bidNtceNo")
    bid_no_ver = t("bidNtceOrd")
    full_bid_no = f"{bid_no_raw}-{bid_no_ver}" if bid_no_ver else bid_no_raw

    start_str = parse_date(t("bidNtceDt")) or ""
    end_str = parse_date(t("bidClseDt")) or ""
    status = determine_status(end_str)

    # 예산/추정가격 파싱
    budget_raw = t("asignBdgtAmt")
    est_price_raw = t("presmptPrce")
    budget = int(float(budget_raw)) if budget_raw else None
    est_price = int(float(est_price_raw)) if est_price_raw else None

    # 첨부파일 (최대 10개)
    attachments = []
    for i in range(1, 11):
        fname = t(f"bidNtceFlNm{i}")
        furl = t(f"bidNtceFlUrl{i}")
        if fname and furl:
            attachments.append({"name": fname, "url": furl})

    # 카테고리
    cat_large = t("prdctClsfcNoNm")
    cat_medium = t("mtrlClsfcNoNm") or t("dtlPrdctClsfcNoNm")
    category = f"{cat_large} > {cat_medium}" if cat_large and cat_medium else cat_large or cat_medium

    url = f"https://www.g2b.go.kr:8081/ep/invitation/publish/bidInfoDtl.do?bidno={bid_no_raw}&bidseq={bid_no_ver}"

    return Notice(
        source="나라장터",
        bid_no=f"{bid_type}-{full_bid_no}",
        title=t("bidNtceNm"),
        organization=t("ntceInsttNm"),
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=url,
        detail_url=url,
        content="",
        budget=budget if budget is not None else est_price,
        region=t("dminsttNm"),
        category=category,
        attachments=attachments or None,
        extra={
            k: v for k, v in {
                "bid_type": bid_type,
                "est_price": est_price,
                "budget": budget,
                "bid_method": t("bidMethdNm"),
                "contract_method": t("cntrctMthdNm"),
                "contact": f"{t('ntceInsttOfclNm')} {t('ntceInsttOfclTelNo')}".strip(),
                "bid_qual": t("bidQlftcRgstDt"),
                "open_date": t("opengDt"),
            }.items() if v is not None and v != ""
        } or None,
    )


class NaraCollector(BaseCollector):
    """나라장터 입찰공고 수집기."""

    source_name = "나라장터"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        bid_types = kwargs.get("bid_types", list(BID_SERVICES.keys()))
        date_ranges = _split_date_range(days)
        notices: list[Notice] = []
        pages_processed = 0

        async with create_client(timeout=30.0) as client:
            for bid_type in bid_types:
                operation = BID_SERVICES[bid_type]
                for start_dt, end_dt in date_ranges:
                    page = 1
                    while True:
                        params = {
                            "serviceKey": self.api_key,
                            "inqryBgnDt": start_dt,
                            "inqryEndDt": end_dt,
                            "numOfRows": str(ROWS_PER_PAGE),
                            "pageNo": str(page),
                            "inqryDiv": "1",
                            "type": "xml",
                        }

                        resp = await self._request_with_retry(
                            client, operation, params, bid_type
                        )
                        if resp is None:
                            break

                        items, total = _parse_xml_items(resp.content)
                        pages_processed += 1

                        for item in items:
                            try:
                                notice = _item_to_notice(item, bid_type)
                                notices.append(notice)
                            except Exception as e:
                                logger.warning(f"[나라장터] 항목 파싱 실패: {e}")

                        # 다음 페이지 확인
                        if page * ROWS_PER_PAGE >= total:
                            break
                        page += 1

        return notices, pages_processed

    async def _request_with_retry(self, client, operation, params, bid_type):
        """429 에러 재시도 포함 API 요청."""
        url = f"{BASE_URL}/{operation}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    logger.warning(
                        f"[나라장터-{bid_type}] 429 에러, {attempt}/{MAX_RETRIES} "
                        f"재시도 ({RETRY_WAIT}초 대기)"
                    )
                    await asyncio.sleep(RETRY_WAIT)
                    continue
                resp.raise_for_status()
                return resp
            except Exception as e:
                if attempt == MAX_RETRIES:
                    logger.error(f"[나라장터-{bid_type}] 요청 실패 ({attempt}회): {e}")
                    return None
                logger.warning(f"[나라장터-{bid_type}] 요청 실패, 재시도: {e}")
                await asyncio.sleep(5)
        return None

    async def health_check(self) -> dict:
        """API 연결 상태 확인 — 용역 서비스 1건 조회."""
        start = time.time()
        try:
            async with create_client(timeout=10.0) as client:
                now = datetime.now()
                params = {
                    "serviceKey": self.api_key,
                    "inqryBgnDt": (now - timedelta(days=1)).strftime("%Y%m%d") + "0000",
                    "inqryEndDt": now.strftime("%Y%m%d") + "2359",
                    "numOfRows": "1",
                    "pageNo": "1",
                    "inqryDiv": "1",
                    "type": "xml",
                }
                resp = await client.get(
                    f"{BASE_URL}/getBidPblancListInfoServcPPSSrch",
                    params=params,
                )
                resp.raise_for_status()
                _parse_xml_items(resp.content)
                ms = int((time.time() - start) * 1000)
                return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}
