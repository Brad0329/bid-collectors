"""나라장터 입찰공고 수집기 + 확장 (낙찰/계약/사전규격).

입찰공고 API: https://apis.data.go.kr/1230000/ad/BidPublicInfoService
낙찰정보 API: https://apis.data.go.kr/1230000/as/ScsbidInfoService
계약정보 API: https://apis.data.go.kr/1230000/ao/CntrctInfoService
사전규격 API: https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService
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

# 낙찰정보 서비스
AWARD_BASE_URL = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
AWARD_SERVICES = {
    "용역": "getScsbidListSttusServcPPSSrch",
    "물품": "getScsbidListSttusThngPPSSrch",
    "공사": "getScsbidListSttusCnstwkPPSSrch",
}

# 계약정보 서비스
CONTRACT_BASE_URL = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"
CONTRACT_SERVICES = {
    "용역": "getCntrctInfoListServc",
    "물품": "getCntrctInfoListThng",
    "공사": "getCntrctInfoListCnstwk",
}

# 사전규격정보 서비스 (주의: ServiceKey 대문자 S)
PRE_SPEC_BASE_URL = "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService"
PRE_SPEC_SERVICES = {
    "용역": "getPublicPrcureThngInfoServc",
    "물품": "getPublicPrcureThngInfoThng",
    "공사": "getPublicPrcureThngInfoCnstwk",
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


def _award_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """낙찰정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    bid_no_raw = t("bidNtceNo")
    bid_no_ver = t("bidNtceOrd")
    full_bid_no = f"{bid_no_raw}-{bid_no_ver}" if bid_no_ver else bid_no_raw

    sucsf_date = parse_date(t("fnlSucsfDate")) or parse_date(t("rlOpengDt")) or ""
    sucsf_amt = t("sucsfbidAmt")
    budget = int(float(sucsf_amt)) if sucsf_amt else None

    url = f"https://www.g2b.go.kr:8081/ep/invitation/publish/bidInfoDtl.do?bidno={bid_no_raw}&bidseq={bid_no_ver}"

    return Notice(
        source="나라장터",
        bid_no=f"낙찰-{bid_type}-{full_bid_no}",
        title=t("bidNtceNm"),
        organization=t("dminsttNm"),
        start_date=sucsf_date or None,
        end_date=None,
        status="closed",
        url=url,
        detail_url=url,
        budget=budget,
        extra={
            k: v for k, v in {
                "bid_type": bid_type,
                "data_type": "낙찰",
                "winner_name": t("bidwinnrNm"),
                "winner_bizno": t("bidwinnrBizno"),
                "winner_ceo": t("bidwinnrCeoNm"),
                "winner_addr": t("bidwinnrAdrs"),
                "winner_tel": t("bidwinnrTelNo"),
                "sucsf_amt": budget,
                "sucsf_rate": t("sucsfbidRate"),
                "open_date": t("rlOpengDt"),
                "participant_count": t("prtcptCnum"),
            }.items() if v is not None and v != ""
        } or None,
    )


def _contract_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """계약정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    cntrct_no = t("dcsnCntrctNo") or t("untyCntrctNo")
    cntrct_date = parse_date(t("cntrctCnclsDate")) or ""
    cntrct_end = parse_date(t("cntrctPrd")) or ""

    amt_raw = t("thtmCntrctAmt") or t("totCntrctAmt")
    budget = int(float(amt_raw)) if amt_raw else None

    detail_url = t("cntrctDtlInfoUrl") or "http://www.g2b.go.kr"

    return Notice(
        source="나라장터",
        bid_no=f"계약-{bid_type}-{cntrct_no}",
        title=t("cntrctNm"),
        organization=t("cntrctInsttNm"),
        start_date=cntrct_date or None,
        end_date=cntrct_end or None,
        status=determine_status(cntrct_end),
        url=detail_url,
        detail_url=detail_url,
        budget=budget,
        region=t("cntrctInsttJrsdctnDivNm"),
        extra={
            k: v for k, v in {
                "bid_type": bid_type,
                "data_type": "계약",
                "bid_no_ref": t("ntceNo"),
                "bsns_div": t("bsnsDivNm"),
                "contract_method": t("cntrctCnclsMthdNm"),
                "contact": f"{t('cntrctInsttOfclNm')} {t('cntrctInsttOfclTelNo')}".strip(),
                "guarantee_rate": t("grntymnyRate"),
                "base_law": t("baseLawNm"),
            }.items() if v is not None and v != ""
        } or None,
    )


def _prespec_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """사전규격정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    ref_no = t("bfSpecRgstNo") or t("refNo")
    rcpt_date = parse_date(t("rcptDt")) or ""
    opinion_close = parse_date(t("opninRgstClseDt")) or ""

    budget_raw = t("asignBdgtAmt")
    budget = int(float(budget_raw)) if budget_raw else None

    # 규격서 첨부파일 (최대 5개)
    attachments = []
    for i in range(1, 6):
        furl = t(f"specDocFileUrl{i}")
        if furl:
            attachments.append({"name": f"규격서{i}", "url": furl})

    return Notice(
        source="나라장터",
        bid_no=f"사전규격-{bid_type}-{ref_no}",
        title=t("prdctClsfcNoNm") or f"사전규격 {ref_no}",
        organization=t("orderInsttNm"),
        start_date=rcpt_date or None,
        end_date=opinion_close or None,
        status=determine_status(opinion_close),
        url=f"https://www.g2b.go.kr",
        detail_url="",
        budget=budget,
        category=t("prdctClsfcNoNm"),
        attachments=attachments or None,
        extra={
            k: v for k, v in {
                "bid_type": bid_type,
                "data_type": "사전규격",
                "rl_dminstt": t("rlDminsttNm"),
                "contact": f"{t('ofclNm')} {t('ofclTelNo')}".strip(),
                "sw_biz": t("swBizObjYn"),
                "delivery_date": t("dlvrTmlmtDt"),
                "delivery_days": t("dlvrDaynum"),
                "bid_ntce_list": t("bidNtceNoList"),
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

    async def _request_with_retry(self, client, operation, params, bid_type, base_url=None):
        """429 에러 재시도 포함 API 요청."""
        url = f"{base_url or BASE_URL}/{operation}"
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

    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회. g2b.go.kr 상세 페이지에서 사업개요 스크래핑.

        나라장터 목록 API에는 content(사업개요)가 없으므로,
        g2b.go.kr 상세 페이지를 스크래핑하여 추가 정보를 가져온다.

        Args:
            bid_no: "용역-R26BK01457928-000" 형식

        Returns:
            dict | None: {"content": str, ...} 또는 None
        """
        try:
            # bid_no 파싱: "용역-R26BK01457928-000"
            parts = bid_no.split("-", 1)
            if len(parts) < 2:
                return None
            bid_no_rest = parts[1]  # "R26BK01457928-000"
            last_dash = bid_no_rest.rfind("-")
            if last_dash == -1:
                ntce_no = bid_no_rest
                ntce_ord = ""
            else:
                ntce_no = bid_no_rest[:last_dash]
                ntce_ord = bid_no_rest[last_dash + 1:]

            # g2b.go.kr 상세 페이지 스크래핑
            detail_url = (
                f"https://www.g2b.go.kr:8081/ep/invitation/publish/"
                f"bidInfoDtl.do?bidno={ntce_no}&bidseq={ntce_ord}"
            )

            async with create_client(timeout=15.0) as client:
                resp = await client.get(detail_url)
                resp.raise_for_status()

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "html.parser")

            # 사업개요 추출 (다양한 셀렉터 시도)
            content = ""
            for selector in [
                "div.detail_cont",
                "td.info_contents",
                "div.bid_detail",
            ]:
                el = soup.select_one(selector)
                if el:
                    from .utils.text import clean_html_to_text
                    content = clean_html_to_text(el.get_text())
                    break

            if not content:
                # 테이블에서 "사업개요" 또는 "공고내용" 행 찾기
                for th in soup.find_all(["th", "dt"]):
                    text = th.get_text(strip=True)
                    if text in ("사업개요", "공고내용", "입찰공고내용"):
                        td = th.find_next(["td", "dd"])
                        if td:
                            from .utils.text import clean_html_to_text
                            content = clean_html_to_text(td.get_text())
                            break

            result = {"content": content} if content else {}

            # 추가 첨부파일 추출
            attachments = []
            for a_tag in soup.select("a[href*='fileDownload'], a[href*='download']"):
                href = a_tag.get("href", "")
                name = a_tag.get_text(strip=True)
                if href and name:
                    if not href.startswith("http"):
                        href = f"https://www.g2b.go.kr{href}"
                    attachments.append({"name": name, "url": href})
            if attachments:
                result["attachments"] = attachments

            return result or None

        except Exception as e:
            logger.warning(f"[나라장터] fetch_detail 실패 ({bid_no}): {e}")
            return None

    async def collect_awards(self, days: int = 1, **kwargs) -> list[Notice]:
        """낙찰정보 수집. 낙찰자, 낙찰금액, 낙찰율 등 포함."""
        return await self._fetch_extended(
            days=days,
            base_url=AWARD_BASE_URL,
            services=AWARD_SERVICES,
            item_converter=_award_item_to_notice,
            label="낙찰",
            service_key_param="serviceKey",
            **kwargs,
        )

    async def collect_contracts(self, days: int = 1, **kwargs) -> list[Notice]:
        """계약정보 수집. 계약명, 계약금액, 계약기간 등 포함."""
        return await self._fetch_extended(
            days=days,
            base_url=CONTRACT_BASE_URL,
            services=CONTRACT_SERVICES,
            item_converter=_contract_item_to_notice,
            label="계약",
            service_key_param="serviceKey",
            **kwargs,
        )

    async def collect_pre_specs(self, days: int = 1, **kwargs) -> list[Notice]:
        """사전규격정보 수집. 규격서, 의견마감일, 배정예산 등 포함."""
        return await self._fetch_extended(
            days=days,
            base_url=PRE_SPEC_BASE_URL,
            services=PRE_SPEC_SERVICES,
            item_converter=_prespec_item_to_notice,
            label="사전규격",
            service_key_param="ServiceKey",  # 대문자 S 필수
            **kwargs,
        )

    async def _fetch_extended(
        self,
        days: int,
        base_url: str,
        services: dict[str, str],
        item_converter,
        label: str,
        service_key_param: str = "serviceKey",
        **kwargs,
    ) -> list[Notice]:
        """확장 서비스 공통 수집 루프."""
        bid_types = kwargs.get("bid_types", list(services.keys()))
        date_ranges = _split_date_range(days)
        notices: list[Notice] = []

        logger.info(f"[나라장터-{label}] 수집 시작: days={days}")

        async with create_client(timeout=30.0) as client:
            for bid_type in bid_types:
                operation = services[bid_type]
                for start_dt, end_dt in date_ranges:
                    page = 1
                    while True:
                        params = {
                            service_key_param: self.api_key,
                            "inqryBgnDt": start_dt,
                            "inqryEndDt": end_dt,
                            "numOfRows": str(ROWS_PER_PAGE),
                            "pageNo": str(page),
                            "inqryDiv": "1",
                            "type": "xml",
                        }

                        resp = await self._request_with_retry(
                            client, operation, params, f"{label}-{bid_type}",
                            base_url=base_url,
                        )
                        if resp is None:
                            break

                        items, total = _parse_xml_items(resp.content)

                        for item in items:
                            try:
                                notice = item_converter(item, bid_type)
                                notices.append(notice)
                            except Exception as e:
                                logger.warning(f"[나라장터-{label}] 항목 파싱 실패: {e}")

                        if page * ROWS_PER_PAGE >= total:
                            break
                        page += 1

        # 중복 제거
        seen = set()
        deduped = []
        for n in notices:
            if n.bid_no not in seen:
                seen.add(n.bid_no)
                deduped.append(n)

        logger.info(f"[나라장터-{label}] 수집 완료: {len(deduped)}건")
        return deduped

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
