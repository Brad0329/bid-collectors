"""나라장터 수집기(nara.py) 단위 테스트."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

import httpx
import respx
from lxml import etree

from bid_collectors.nara import (
    _split_date_range,
    _parse_xml_items,
    _item_to_notice,
    NaraCollector,
    BASE_URL,
    BID_SERVICES,
    ROWS_PER_PAGE,
)


# ---------------------------------------------------------------------------
# Sample XML helpers
# ---------------------------------------------------------------------------

def _make_xml_response(items_xml: str = "", total_count: int = 1,
                       result_code: str = "00", result_msg: str = "NORMAL SERVICE.") -> bytes:
    """테스트용 XML 응답 생성."""
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>{result_code}</resultCode><resultMsg>{result_msg}</resultMsg></header>
  <body>
    <totalCount>{total_count}</totalCount>
    <items>{items_xml}</items>
  </body>
</response>""".encode("utf-8")


SAMPLE_ITEM_XML = """\
<item>
  <bidNtceNo>20260405001</bidNtceNo>
  <bidNtceOrd>00</bidNtceOrd>
  <bidNtceNm>테스트 용역 입찰공고</bidNtceNm>
  <ntceInsttNm>테스트기관</ntceInsttNm>
  <bidNtceDt>2026/04/05</bidNtceDt>
  <bidClseDt>2026/04/20</bidClseDt>
  <presmptPrce>50000000</presmptPrce>
  <asignBdgtAmt>60000000</asignBdgtAmt>
  <bidMethdNm>제한경쟁</bidMethdNm>
  <cntrctMthdNm>총액계약</cntrctMthdNm>
  <prdctClsfcNoNm>용역</prdctClsfcNoNm>
  <dtlPrdctClsfcNoNm>학술연구</dtlPrdctClsfcNoNm>
  <dminsttNm>서울</dminsttNm>
  <ntceInsttOfclNm>홍길동</ntceInsttOfclNm>
  <ntceInsttOfclTelNo>02-1234-5678</ntceInsttOfclTelNo>
  <bidNtceFlNm1>첨부파일1.pdf</bidNtceFlNm1>
  <bidNtceFlUrl1>https://example.com/file1.pdf</bidNtceFlUrl1>
  <bidNtceFlNm2>첨부파일2.hwp</bidNtceFlNm2>
  <bidNtceFlUrl2>https://example.com/file2.hwp</bidNtceFlUrl2>
  <opengDt>202604200900</opengDt>
</item>"""


SAMPLE_RESPONSE = _make_xml_response(SAMPLE_ITEM_XML, total_count=1)


# ---------------------------------------------------------------------------
# _split_date_range tests
# ---------------------------------------------------------------------------

class TestSplitDateRange:
    """_split_date_range 함수 테스트."""

    def test_days_1_returns_single_range(self):
        ranges = _split_date_range(1)
        assert len(ranges) == 1
        start, end = ranges[0]
        assert start.endswith("0000")
        assert end.endswith("2359")
        assert len(start) == 12  # yyyyMMddHHmm
        assert len(end) == 12

    def test_days_10_returns_two_ranges(self):
        """10일은 7일 + 3일로 분할."""
        ranges = _split_date_range(10)
        assert len(ranges) == 2

    def test_days_14_returns_two_ranges(self):
        """14일은 정확히 7+7 = 2개."""
        ranges = _split_date_range(14)
        assert len(ranges) == 2

    def test_days_15_returns_three_ranges(self):
        """15일은 7+7+1 = 3개."""
        ranges = _split_date_range(15)
        assert len(ranges) == 3

    def test_format_yyyyMMddHHmm(self):
        ranges = _split_date_range(1)
        start, end = ranges[0]
        # 순수 숫자 12자리
        assert start.isdigit() and len(start) == 12
        assert end.isdigit() and len(end) == 12

    def test_ranges_are_contiguous(self):
        """분할된 범위들의 끝과 시작이 연속적."""
        ranges = _split_date_range(20)
        for i in range(len(ranges) - 1):
            # 현재 chunk의 end 날짜(8자리)와 다음 chunk의 start 날짜(8자리) 일치
            cur_end_date = ranges[i][1][:8]
            next_start_date = ranges[i + 1][0][:8]
            assert cur_end_date == next_start_date


# ---------------------------------------------------------------------------
# _parse_xml_items tests
# ---------------------------------------------------------------------------

class TestParseXmlItems:
    """_parse_xml_items 함수 테스트."""

    def test_valid_xml_returns_items_and_count(self):
        items, total = _parse_xml_items(SAMPLE_RESPONSE)
        assert total == 1
        assert len(items) == 1
        assert items[0].findtext("bidNtceNo") == "20260405001"

    def test_multiple_items(self):
        two_items = SAMPLE_ITEM_XML + SAMPLE_ITEM_XML
        xml = _make_xml_response(two_items, total_count=2)
        items, total = _parse_xml_items(xml)
        assert total == 2
        assert len(items) == 2

    def test_error_response_raises_valueerror(self):
        xml = _make_xml_response(
            result_code="99",
            result_msg="SERVICE_KEY_IS_NOT_REGISTERED_ERROR.",
            total_count=0,
        )
        with pytest.raises(ValueError, match="API 에러: 99"):
            _parse_xml_items(xml)

    def test_empty_response_returns_empty(self):
        xml = _make_xml_response("", total_count=0)
        items, total = _parse_xml_items(xml)
        assert total == 0
        assert len(items) == 0


# ---------------------------------------------------------------------------
# _item_to_notice tests
# ---------------------------------------------------------------------------

class TestItemToNotice:
    """_item_to_notice 함수 테스트."""

    def _make_item(self, xml_str: str = SAMPLE_ITEM_XML) -> etree._Element:
        return etree.fromstring(xml_str)

    def test_full_item_mapping(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")

        assert notice.source == "나라장터"
        assert notice.title == "테스트 용역 입찰공고"
        assert notice.organization == "테스트기관"
        assert notice.region == "서울"

    def test_bid_no_format(self):
        """bid_no는 '{bid_type}-{bidNtceNo}-{bidNtceOrd}' 형식."""
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.bid_no == "용역-20260405001-00"

    def test_bid_no_without_ord(self):
        """bidNtceOrd가 없으면 bidNtceNo만."""
        xml = "<item><bidNtceNo>12345</bidNtceNo><bidNtceNm>테스트</bidNtceNm><ntceInsttNm>기관</ntceInsttNm></item>"
        item = etree.fromstring(xml)
        notice = _item_to_notice(item, "물품")
        assert notice.bid_no == "물품-12345"

    def test_url_construction(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert "bidno=20260405001" in notice.url
        assert "bidseq=00" in notice.url
        assert notice.url.startswith("https://www.g2b.go.kr")

    def test_budget_and_est_price(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        # budget = asignBdgtAmt (60000000), est_price = presmptPrce (50000000)
        # notice.budget = budget or est_price → 60000000
        assert notice.budget == 60000000
        assert notice.extra["budget"] == 60000000
        assert notice.extra["est_price"] == 50000000

    def test_attachments_parsing(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.attachments is not None
        assert len(notice.attachments) == 2
        assert notice.attachments[0]["name"] == "첨부파일1.pdf"
        assert notice.attachments[0]["url"] == "https://example.com/file1.pdf"
        assert notice.attachments[1]["name"] == "첨부파일2.hwp"

    def test_category_combined(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.category == "용역 > 학술연구"

    def test_extra_fields(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.extra["bid_type"] == "용역"
        assert notice.extra["bid_method"] == "제한경쟁"
        assert notice.extra["contract_method"] == "총액계약"
        assert "홍길동" in notice.extra["contact"]
        assert "02-1234-5678" in notice.extra["contact"]
        assert notice.extra["open_date"] == "202604200900"

    def test_empty_fields_handled_gracefully(self):
        """최소한의 필드만 있는 item도 에러 없이 변환."""
        xml = "<item><bidNtceNo>99999</bidNtceNo><bidNtceNm>최소공고</bidNtceNm><ntceInsttNm>기관</ntceInsttNm></item>"
        item = etree.fromstring(xml)
        notice = _item_to_notice(item, "공사")
        assert notice.bid_no == "공사-99999"
        assert notice.title == "최소공고"
        assert notice.attachments is None
        assert notice.budget is None

    def test_dates_parsed(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        # bidNtceDt=2026/04/05, bidClseDt=2026/04/20
        assert notice.start_date is not None
        assert str(notice.start_date) == "2026-04-05"
        assert notice.end_date is not None
        assert str(notice.end_date) == "2026-04-20"


# ---------------------------------------------------------------------------
# NaraCollector init tests
# ---------------------------------------------------------------------------

class TestNaraCollectorInit:
    """NaraCollector 초기화 테스트."""

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DATA_GO_KR_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    NaraCollector()

    def test_source_name(self):
        collector = NaraCollector(api_key="test-key")
        assert collector.source_name == "나라장터"

    def test_api_key_from_constructor(self):
        collector = NaraCollector(api_key="my-key")
        assert collector.api_key == "my-key"


# ---------------------------------------------------------------------------
# NaraCollector._fetch mock tests
# ---------------------------------------------------------------------------

class TestNaraCollectorFetch:
    """NaraCollector._fetch HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_response(self):
        """단일 페이지 응답 → 올바른 Notice 반환."""
        # 3개 서비스 모두 모킹 (용역만 데이터 있음)
        for operation in BID_SERVICES.values():
            respx.get(f"{BASE_URL}/{operation}").mock(
                return_value=httpx.Response(200, content=SAMPLE_RESPONSE)
            )

        collector = NaraCollector(api_key="test-key")
        kwargs = {}
        notices, pages = await collector._fetch(days=1, bid_types=["용역"], **kwargs)
        assert len(notices) == 1
        assert notices[0].title == "테스트 용역 입찰공고"

    @pytest.mark.asyncio
    @respx.mock
    async def test_multi_page_pagination(self):
        """totalCount > 100 → 여러 페이지 요청."""
        # 페이지 1: 100건 중 1건 (간략화), totalCount=150
        page1_items = SAMPLE_ITEM_XML  # 1건
        page1_xml = _make_xml_response(page1_items, total_count=150)

        # 페이지 2: 나머지
        page2_item = SAMPLE_ITEM_XML.replace("20260405001", "20260405002")
        page2_xml = _make_xml_response(page2_item, total_count=150)

        operation = BID_SERVICES["용역"]
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, content=page1_xml)
            else:
                return httpx.Response(200, content=page2_xml)

        respx.get(f"{BASE_URL}/{operation}").mock(side_effect=side_effect)

        collector = NaraCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1, bid_types=["용역"])
        # 1건/페이지 * 2페이지 (totalCount=150 > ROWS_PER_PAGE=100이므로 페이지 2도 요청)
        assert call_count == 2
        assert len(notices) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_response(self):
        """빈 응답 → 빈 리스트."""
        empty_xml = _make_xml_response("", total_count=0)
        for operation in BID_SERVICES.values():
            respx.get(f"{BASE_URL}/{operation}").mock(
                return_value=httpx.Response(200, content=empty_xml)
            )

        collector = NaraCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1, bid_types=["용역"])
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_retry_logic(self):
        """429 에러 → 재시도 후 성공."""
        operation = BID_SERVICES["용역"]
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429)
            return httpx.Response(200, content=SAMPLE_RESPONSE)

        respx.get(f"{BASE_URL}/{operation}").mock(side_effect=side_effect)

        collector = NaraCollector(api_key="test-key")
        with patch("bid_collectors.nara.asyncio.sleep", new_callable=AsyncMock):
            notices, pages = await collector._fetch(days=1, bid_types=["용역"])

        assert call_count == 2
        assert len(notices) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_exhausts_retries(self):
        """429가 MAX_RETRIES까지 반복 → None 반환 (빈 결과)."""
        operation = BID_SERVICES["용역"]
        respx.get(f"{BASE_URL}/{operation}").mock(
            return_value=httpx.Response(429)
        )

        collector = NaraCollector(api_key="test-key")
        with patch("bid_collectors.nara.asyncio.sleep", new_callable=AsyncMock):
            notices, pages = await collector._fetch(days=1, bid_types=["용역"])

        assert notices == []


# ---------------------------------------------------------------------------
# NaraCollector.health_check mock tests
# ---------------------------------------------------------------------------

class TestNaraCollectorHealthCheck:
    """health_check HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        """정상 응답 → status 'ok'."""
        respx.get(f"{BASE_URL}/getBidPblancListInfoServcPPSSrch").mock(
            return_value=httpx.Response(200, content=SAMPLE_RESPONSE)
        )

        collector = NaraCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "나라장터"
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_error(self):
        """실패 응답 → status 'error'."""
        respx.get(f"{BASE_URL}/getBidPblancListInfoServcPPSSrch").mock(
            return_value=httpx.Response(500)
        )

        collector = NaraCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "나라장터"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_api_error_response(self):
        """API 에러 XML 응답 → status 'error'."""
        error_xml = _make_xml_response(result_code="99", result_msg="KEY_ERROR", total_count=0)
        respx.get(f"{BASE_URL}/getBidPblancListInfoServcPPSSrch").mock(
            return_value=httpx.Response(200, content=error_xml)
        )

        collector = NaraCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert "99" in result["message"]
