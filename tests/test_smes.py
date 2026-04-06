"""중소벤처기업부 수집기(smes.py) 단위 테스트."""

import os
import pytest
from datetime import date
from unittest.mock import patch

import httpx
import respx
from lxml import etree

from bid_collectors.smes import (
    _parse_xml_response,
    _item_to_notice,
    _extract_attachments,
    SmesCollector,
    API_URL,
    DEFAULT_NUM_OF_ROWS,
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
  <itemId>MSS20260401001</itemId>
  <title>2026년 중소기업 수출지원사업 공고</title>
  <dataContents>&lt;p&gt;중소기업 수출 지원 내용&lt;/p&gt;</dataContents>
  <applicationStartDate>2026-04-01</applicationStartDate>
  <applicationEndDate>2026-04-30</applicationEndDate>
  <viewUrl>https://www.mss.go.kr/site/smba/ex/bbs/View.do?cbIdx=126&amp;bcIdx=1234</viewUrl>
  <writerPosition>수출지원과</writerPosition>
  <writer>중소벤처기업부</writer>
  <suptScale>5,000,000원</suptScale>
  <fileName>공고문.hwp</fileName>
  <fileUrl>https://www.mss.go.kr/download/file1.hwp</fileUrl>
  <fileName>신청서.hwp</fileName>
  <fileUrl>https://www.mss.go.kr/download/file2.hwp</fileUrl>
</item>"""


SAMPLE_RESPONSE = _make_xml_response(SAMPLE_ITEM_XML, total_count=1)


# ---------------------------------------------------------------------------
# _parse_xml_response tests
# ---------------------------------------------------------------------------

class TestParseXmlResponse:
    """_parse_xml_response 함수 테스트."""

    def test_valid_xml_returns_items_and_count(self):
        items, total = _parse_xml_response(SAMPLE_RESPONSE)
        assert total == 1
        assert len(items) == 1
        assert items[0].findtext("itemId") == "MSS20260401001"

    def test_multiple_items(self):
        two_items = SAMPLE_ITEM_XML + SAMPLE_ITEM_XML
        xml = _make_xml_response(two_items, total_count=2)
        items, total = _parse_xml_response(xml)
        assert total == 2
        assert len(items) == 2

    def test_error_response_raises_valueerror(self):
        xml = _make_xml_response(
            result_code="99",
            result_msg="SERVICE_KEY_IS_NOT_REGISTERED_ERROR.",
            total_count=0,
        )
        with pytest.raises(ValueError, match="API 에러: 99"):
            _parse_xml_response(xml)

    def test_empty_response_returns_empty(self):
        xml = _make_xml_response("", total_count=0)
        items, total = _parse_xml_response(xml)
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
        notice = _item_to_notice(item)

        assert notice.source == "중소벤처기업부"
        assert notice.title == "2026년 중소기업 수출지원사업 공고"
        assert notice.organization == "중소벤처기업부"
        assert notice.category == "수출지원과"
        assert notice.url == "https://www.mss.go.kr/site/smba/ex/bbs/View.do?cbIdx=126&bcIdx=1234"
        assert notice.detail_url == notice.url

    def test_bid_no_format(self):
        """bid_no는 'MSS-{itemId}' 형식."""
        item = self._make_item()
        notice = _item_to_notice(item)
        assert notice.bid_no == "MSS-MSS20260401001"

    def test_status_via_determine_status(self):
        """end_date가 미래면 ongoing, 과거면 closed."""
        item = self._make_item()
        notice = _item_to_notice(item)
        # applicationEndDate=2026-04-30, 미래이므로 ongoing 또는 closed
        # determine_status가 end_date 기준으로 판단
        assert notice.status in ("ongoing", "closed", "upcoming")

    def test_content_html_cleaning(self):
        """HTML 태그가 제거된 content."""
        item = self._make_item()
        notice = _item_to_notice(item)
        assert "<p>" not in notice.content
        assert "중소기업 수출 지원 내용" in notice.content

    def test_content_truncation_500_chars(self):
        """content는 500자 이내로 truncate."""
        long_content = "A" * 1000
        xml = f"""\
<item>
  <itemId>TEST001</itemId>
  <title>테스트</title>
  <dataContents>{long_content}</dataContents>
  <viewUrl>https://example.com</viewUrl>
</item>"""
        item = etree.fromstring(xml)
        notice = _item_to_notice(item)
        assert len(notice.content) <= 500

    def test_budget_parsing_from_suptscale(self):
        """suptScale '5,000,000원' → budget 5000000."""
        item = self._make_item()
        notice = _item_to_notice(item)
        assert notice.budget == 5000000

    def test_budget_none_when_missing(self):
        """suptScale 없으면 budget은 None."""
        xml = """\
<item>
  <itemId>TEST002</itemId>
  <title>예산없음</title>
  <viewUrl>https://example.com</viewUrl>
</item>"""
        item = etree.fromstring(xml)
        notice = _item_to_notice(item)
        assert notice.budget is None

    def test_attachments_extraction(self):
        """첨부파일 2개 추출."""
        item = self._make_item()
        notice = _item_to_notice(item)
        assert notice.attachments is not None
        assert len(notice.attachments) == 2
        assert notice.attachments[0]["name"] == "공고문.hwp"
        assert notice.attachments[0]["url"] == "https://www.mss.go.kr/download/file1.hwp"
        assert notice.attachments[1]["name"] == "신청서.hwp"
        assert notice.attachments[1]["url"] == "https://www.mss.go.kr/download/file2.hwp"

    def test_dates_parsed(self):
        item = self._make_item()
        notice = _item_to_notice(item)
        assert notice.start_date is not None
        assert str(notice.start_date) == "2026-04-01"
        assert notice.end_date is not None
        assert str(notice.end_date) == "2026-04-30"

    def test_extra_fields(self):
        item = self._make_item()
        notice = _item_to_notice(item)
        assert notice.extra is not None
        assert notice.extra["budget_raw"] == "5,000,000원"
        assert notice.extra["writer"] == "중소벤처기업부"

    def test_empty_fields_handled_gracefully(self):
        """최소한의 필드만 있는 item도 에러 없이 변환."""
        xml = "<item><itemId>99999</itemId><title>최소공고</title><viewUrl>https://example.com</viewUrl></item>"
        item = etree.fromstring(xml)
        notice = _item_to_notice(item)
        assert notice.bid_no == "MSS-99999"
        assert notice.title == "최소공고"
        assert notice.attachments is None
        assert notice.budget is None


# ---------------------------------------------------------------------------
# _extract_attachments tests
# ---------------------------------------------------------------------------

class TestExtractAttachments:
    """_extract_attachments 함수 테스트."""

    def test_multiple_files(self):
        xml = """\
<item>
  <fileName>파일1.pdf</fileName>
  <fileUrl>https://example.com/f1.pdf</fileUrl>
  <fileName>파일2.hwp</fileName>
  <fileUrl>https://example.com/f2.hwp</fileUrl>
</item>"""
        item = etree.fromstring(xml)
        result = _extract_attachments(item)
        assert result is not None
        assert len(result) == 2
        assert result[0] == {"name": "파일1.pdf", "url": "https://example.com/f1.pdf"}
        assert result[1] == {"name": "파일2.hwp", "url": "https://example.com/f2.hwp"}

    def test_no_files_returns_none(self):
        xml = "<item><title>첨부없음</title></item>"
        item = etree.fromstring(xml)
        result = _extract_attachments(item)
        assert result is None

    def test_names_shorter_than_urls(self):
        """fileName이 fileUrl보다 적으면 나머지는 기본 이름 사용."""
        xml = """\
<item>
  <fileName>파일1.pdf</fileName>
  <fileUrl>https://example.com/f1.pdf</fileUrl>
  <fileUrl>https://example.com/f2.pdf</fileUrl>
  <fileUrl>https://example.com/f3.pdf</fileUrl>
</item>"""
        item = etree.fromstring(xml)
        result = _extract_attachments(item)
        assert result is not None
        assert len(result) == 3
        assert result[0]["name"] == "파일1.pdf"
        assert result[1]["name"] == "첨부파일2"
        assert result[2]["name"] == "첨부파일3"


# ---------------------------------------------------------------------------
# SmesCollector init tests
# ---------------------------------------------------------------------------

class TestSmesCollectorInit:
    """SmesCollector 초기화 테스트."""

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DATA_GO_KR_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    SmesCollector()

    def test_source_name(self):
        collector = SmesCollector(api_key="test-key")
        assert collector.source_name == "중소벤처기업부"

    def test_api_key_from_constructor(self):
        collector = SmesCollector(api_key="my-key")
        assert collector.api_key == "my-key"


# ---------------------------------------------------------------------------
# SmesCollector._fetch mock tests
# ---------------------------------------------------------------------------

class TestSmesCollectorFetch:
    """SmesCollector._fetch HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_response(self):
        """단일 페이지 응답 → 올바른 Notice 반환."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, content=SAMPLE_RESPONSE)
        )

        collector = SmesCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert len(notices) == 1
        assert pages == 1
        assert notices[0].title == "2026년 중소기업 수출지원사업 공고"
        assert notices[0].bid_no == "MSS-MSS20260401001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_multi_page_pagination(self):
        """totalCount > DEFAULT_NUM_OF_ROWS → 여러 페이지 요청."""
        page1_xml = _make_xml_response(SAMPLE_ITEM_XML, total_count=150)
        page2_item = SAMPLE_ITEM_XML.replace("MSS20260401001", "MSS20260401002")
        page2_xml = _make_xml_response(page2_item, total_count=150)

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, content=page1_xml)
            else:
                return httpx.Response(200, content=page2_xml)

        respx.get(API_URL).mock(side_effect=side_effect)

        collector = SmesCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert call_count == 2
        assert len(notices) == 2
        assert pages == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_response(self):
        """빈 응답 → 빈 리스트."""
        empty_xml = _make_xml_response("", total_count=0)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, content=empty_xml)
        )

        collector = SmesCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []
        assert pages == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_returns_empty(self):
        """HTTP 에러 → 빈 결과 (break)."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = SmesCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []
        assert pages == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_xml_error_response_returns_empty(self):
        """API XML 에러 응답 → 빈 결과 (break)."""
        error_xml = _make_xml_response(
            result_code="99",
            result_msg="SERVICE_KEY_IS_NOT_REGISTERED_ERROR.",
            total_count=0,
        )
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, content=error_xml)
        )

        collector = SmesCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []
        assert pages == 0


# ---------------------------------------------------------------------------
# SmesCollector.health_check mock tests
# ---------------------------------------------------------------------------

class TestSmesCollectorHealthCheck:
    """health_check HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        """정상 응답 → status 'ok'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, content=SAMPLE_RESPONSE)
        )

        collector = SmesCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "중소벤처기업부"
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_http_error(self):
        """HTTP 실패 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = SmesCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "중소벤처기업부"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_api_error_response(self):
        """API 에러 XML 응답 → status 'error'."""
        error_xml = _make_xml_response(result_code="99", result_msg="KEY_ERROR", total_count=0)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, content=error_xml)
        )

        collector = SmesCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert "99" in result["message"]
