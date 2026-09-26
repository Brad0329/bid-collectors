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
from collections import Counter
from datetime import datetime, timedelta

from lxml import etree

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import CANCEL_KIND, determine_status

logger = logging.getLogger("bid_collectors")

BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# 서비스별 operation
BID_SERVICES = {
    "용역": "getBidPblancListInfoServcPPSSrch",
    "물품": "getBidPblancListInfoThngPPSSrch",
    "공사": "getBidPblancListInfoCnstwkPPSSrch",
}
# 입찰공고 category 원문 태그(업무별 한 필드 — 아래 _item_to_notice 주석)
CATEGORY_TAGS = {"용역": "pubPrcrmntLrgClsfcNm", "물품": "dtilPrdctClsfcNoNm", "공사": "mainCnsttyNm"}

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
    require_fields(bidNtceNo=bid_no_raw, bidNtceNm=t("bidNtceNm"))
    bid_no_ver = t("bidNtceOrd")
    full_bid_no = f"{bid_no_raw}-{bid_no_ver}" if bid_no_ver else bid_no_raw

    start_str = parse_date(t("bidNtceDt")) or ""
    end_str = parse_date(t("bidClseDt")) or ""
    status = determine_status(end_str, cancelled=t("ntceKindNm") == CANCEL_KIND)

    # 예산 = 배정예산 원문 한 필드(v1.6.0, 원칙 ②). 태그가 업무마다 다르다 — 용역·물품 asignBdgtAmt, 공사 bdgtAmt.
    # 종전엔 없으면 추정가격(presmptPrce)으로 대체했다 — 추정가격은 extra 원문에 있다
    budget_raw = t("bdgtAmt" if bid_type == "공사" else "asignBdgtAmt")
    budget = int(float(budget_raw)) if budget_raw else None

    # 첨부파일: 공고규격서. 종전의 공고첨부 bidNtceFlNm/Url{i} 루프는 명세·실측(용역/물품/공사 1310/1108/942건)에 없는 태그라 삭제(v1.3.1).
    # 그 밖의 첨부(e발주 등)는 목록 응답에 없고 별도 오퍼레이션(getBidPblancListInfoEorderAtchFileInfo)에만 있다
    attachments = []
    for i in range(1, 11):
        furl = t(f"ntceSpecDocUrl{i}")
        if furl:
            fname = t(f"ntceSpecFileNm{i}") or f"규격서{i}"
            attachments.append({"name": fname, "url": furl})

    # 카테고리: 업무구분마다 분류 태그가 다르다 (2026-09-24 실측, 구분별 목록 첫 100건) — 원문 한 필드(v1.6.0, 원칙 ②)
    #   용역 = 공공조달분류 대분류 pubPrcrmntLrgClsfcNm (100%, 중분류는 extra) / 물품 = 세부품명 dtilPrdctClsfcNoNm (100%)
    #   공사 = 주공종 mainCnsttyNm (26% — 나머지는 빈값). 종전엔 "대 > 중" 합성과 다른 분류로의 폴백이 있었다.
    category = t(CATEGORY_TAGS[bid_type]) if bid_type in CATEGORY_TAGS else ""

    # URL: API 제공 URL 우선, 없으면 폴백
    fallback_url = f"https://www.g2b.go.kr:8081/ep/invitation/publish/bidInfoDtl.do?bidno={bid_no_raw}&bidseq={bid_no_ver}"
    url = t("bidNtceDtlUrl") or fallback_url

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
        budget=budget,
        # 지역 = 공사현장지역(원문 지역 필드). 용역·물품엔 지역 필드가 없다 — 종전 수요기관명(dminsttNm)은 지역이 아니었다(v1.6.0)
        region=t("cnstrtsiteRgnNm") if bid_type == "공사" else "",
        category=category,
        attachments=attachments or None,
        # 원문 전부(v1.2.5, 원칙 ①) — 입찰방식·낙찰방법·담당자·평가비율 등은 응답 태그 이름 그대로 extra에 있다.
        # 종전 손 매핑은 실제 응답 태그의 1/3만 읽었고 오타(bidQlftcRgstDt)·없는 태그(cntrctMthdNm)로 항상 빈 키가 있었다.
        extra=raw_fields(item),
    )


def _award_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """낙찰정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    bid_no_raw = t("bidNtceNo")
    require_fields(bidNtceNo=bid_no_raw, bidNtceNm=t("bidNtceNm"))  # 빈 ID는 `낙찰-용역-`로 합쳐진다(v1.2.5 A)
    bid_no_ver = t("bidNtceOrd")
    full_bid_no = f"{bid_no_raw}-{bid_no_ver}" if bid_no_ver else bid_no_raw

    # 최종낙찰일만(v1.6.0 — 종전 실개찰일 rlOpengDt 폴백 제거). 낙찰금액 sucsfbidAmt는 예산이 아니라 budget에 넣지 않는다(extra 원문)
    sucsf_date = parse_date(t("fnlSucsfDate")) or ""

    url = f"https://www.g2b.go.kr:8081/ep/invitation/publish/bidInfoDtl.do?bidno={bid_no_raw}&bidseq={bid_no_ver}"

    return Notice(
        source="나라장터",
        bid_no=f"낙찰-{bid_type}-{full_bid_no}",
        title=t("bidNtceNm"),
        organization=t("dminsttNm"),
        start_date=sucsf_date or None,
        end_date=None,
        status="closed",  # 낙찰 = 끝난 입찰 — 원칙 ②의 명시적 예외(편의 계산값, CONTRACT 2026-09-26)
        url=url,
        detail_url=url,
        budget=None,
        extra=raw_fields(item),  # 원문 전부(v1.2.5) — 낙찰자·낙찰률·참가자 수는 응답 태그 이름 그대로
    )


def _contract_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """계약정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    cntrct_no = t("dcsnCntrctNo") or t("untyCntrctNo")
    # 제목 태그가 업무마다 다르다 — 용역·물품 cntrctNm, 공사 cnstwkNm(명세·2026-09-25 실측 100/40/61건 전부).
    # 종전엔 cntrctNm만 봐서 공사가 전건 건너뛰어졌다(v1.3.1)
    title = t("cntrctNm") or t("cnstwkNm")
    require_fields(cntrctNo=cntrct_no, cntrctNm=title)  # 빈 ID는 `계약-용역-`로 합쳐진다(v1.2.5 A)
    cntrct_date = parse_date(t("cntrctCnclsDate")) or ""
    # end_date 없음(v1.6.0) — 종전 cntrctPrd(계약기간)는 마감일이 아니고, 기간 문자열의 시작일이 들어갔다(부록 A-2). 원문은 extra

    # 금차 계약금액 원문 한 필드(v1.6.0 — 종전 총계약금액 totCntrctAmt 폴백 제거, extra 원문)
    amt_raw = t("thtmCntrctAmt")
    budget = int(float(amt_raw)) if amt_raw else None

    detail_url = t("cntrctDtlInfoUrl") or "http://www.g2b.go.kr"

    return Notice(
        source="나라장터",
        bid_no=f"계약-{bid_type}-{cntrct_no}",
        title=title,
        organization=t("cntrctInsttNm"),
        start_date=cntrct_date or None,
        end_date=None,
        status=determine_status(None),
        url=detail_url,
        detail_url=detail_url,
        budget=budget,
        region="",  # 종전 cntrctInsttJrsdctnDivNm은 기관 관할 구분(국가기관 등)이라 지역이 아니다(v1.6.0)
        extra=raw_fields(item),  # 원문 전부(v1.2.5)
    )


def _prespec_item_to_notice(item: etree._Element, bid_type: str) -> Notice:
    """사전규격정보 XML item → Notice."""

    def t(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    ref_no = t("bfSpecRgstNo") or t("refNo")
    require_fields(bfSpecRgstNo=ref_no)  # 빈 ID는 `사전규격-용역-`로 합쳐진다(v1.2.5 A). 제목은 품명 폴백이 있어 검사하지 않는다
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
        url="https://www.g2b.go.kr",
        detail_url="",
        budget=budget,
        category=t("prdctClsfcNoNm"),
        attachments=attachments or None,
        extra=raw_fields(item),  # 원문 전부(v1.2.5)
    )


class NaraCollector(BaseCollector):
    """나라장터 입찰공고 수집기."""

    source_name = "나라장터"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        bid_types = kwargs.get("bid_types", list(BID_SERVICES.keys()))
        date_ranges = _split_date_range(days)
        notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        skips: Counter[str] = Counter()

        async with create_client(timeout=30.0) as client:
            for bid_type in bid_types:
                operation = BID_SERVICES[bid_type]
                api_error = False
                for start_dt, end_dt in date_ranges:
                    if api_error:
                        break
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

                        where = f"{bid_type} {start_dt[:8]}~{end_dt[:8]} 페이지 {page}"
                        try:
                            resp = await self._request_with_retry(
                                client, operation, params, bid_type
                            )
                        except RuntimeError as e:
                            # 네트워크·HTTP 실패는 이 기간만 포기하고 다음 기간은 시도한다
                            errors.append(f"{where}: {e}")
                            break
                        try:
                            items, total = _parse_xml_items(resp.content)
                        except (ValueError, etree.XMLSyntaxError) as e:
                            # resultCode 에러(쿼터 초과 등)는 같은 서비스의 남은 기간도 실패한다 — 서비스 단위로 중단,
                            # 앞서 모은 다른 서비스 결과는 보존한다
                            msg = self._mask(f"{where}: {e} — {bid_type} 남은 기간 중단")
                            logger.error(f"[나라장터] {msg}")
                            errors.append(msg)
                            api_error = True
                            break
                        pages_processed += 1

                        for item in items:
                            try:
                                notice = _item_to_notice(item, bid_type)
                            except Exception as e:
                                self._record_skip(skips, e, item)
                                continue
                            notices.append(notice)

                        # 다음 페이지 확인
                        if page * ROWS_PER_PAGE >= total:
                            break
                        page += 1

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages_processed, errors

    async def _request_with_retry(self, client, operation, params, bid_type, base_url=None):
        """429 에러 재시도 포함 API 요청.

        Raises:
            RuntimeError: 재시도 소진. 메시지의 API 키는 가려져 있다
                (원래 예외는 키가 든 URL을 담고 있어 연결하지 않는다).
        """
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
                reason = self._mask(f"{type(e).__name__}: {e}")
                if attempt == MAX_RETRIES:
                    logger.error(f"[나라장터-{bid_type}] 요청 실패 ({attempt}회): {reason}")
                    raise RuntimeError(f"요청 실패 ({attempt}회): {reason}") from None
                logger.warning(f"[나라장터-{bid_type}] 요청 실패, 재시도: {reason}")
                await asyncio.sleep(5)
        logger.error(f"[나라장터-{bid_type}] 429 재시도 {MAX_RETRIES}회 소진")
        raise RuntimeError(f"429 재시도 {MAX_RETRIES}회 소진")

    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회.

        나라장터 data.go.kr API는 bidNtceNo 단건 조회를 지원하지 않으며,
        사업개요(content) 필드도 제공하지 않는다.
        대신 수집 시점(_item_to_notice)에서 lets_portal과 동일한 수준의
        상세 필드(평가비율, 낙찰방식, 담당자이메일, 조달분류 등)를
        Notice.extra에 이미 저장하므로, 추가 보충할 데이터가 없다.

        Args:
            bid_no: "용역-R26BK01457928-000" 형식

        Returns:
            None (API 단건 조회 미지원)
        """
        logger.debug(
            f"[나라장터] fetch_detail 스킵 ({bid_no}): "
            "API 단건 조회 미지원, 수집 시 extra에 상세 필드 포함됨"
        )
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
        skips: Counter[str] = Counter()

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

                        # 실패는 예외로 호출자에게 간다(반환형 list[Notice]라 errors를 담을 곳이 없다 — CONTRACT.md)
                        resp = await self._request_with_retry(
                            client, operation, params, f"{label}-{bid_type}",
                            base_url=base_url,
                        )

                        items, total = _parse_xml_items(resp.content)

                        for item in items:
                            try:
                                notice = item_converter(item, bid_type)
                            except Exception as e:
                                # 그 항목만 건너뛴다 — ID 없는 항목을 통과시키면 아래 중복 제거가 서로 다른 공고를 1건으로 합친다(v1.2.5 A)
                                self._record_skip(skips, e, item)
                                continue
                            notices.append(notice)

                        if page * ROWS_PER_PAGE >= total:
                            break
                        page += 1

        if skip_msg := self._skip_message(skips):
            # 반환형이 list[Notice]라 errors 채널이 없다(CONTRACT.md — 채널 추가는 선택 인자 = minor, plan.md 보류) — 경고 로그가 유일한 보고 자리
            logger.warning(f"[나라장터-{label}] {skip_msg}")

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
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)), "response_time_ms": ms}
