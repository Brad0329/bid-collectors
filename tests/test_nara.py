"""나라장터 수집기(nara.py) 단위 테스트."""

import logging
import os
import pytest
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
  <pubPrcrmntLrgClsfcNm>ICT 서비스</pubPrcrmntLrgClsfcNm>
  <pubPrcrmntMidClsfcNm>ICT사업 컨설팅</pubPrcrmntMidClsfcNm>
  <dminsttNm>서울</dminsttNm>
  <ntceInsttOfclNm>홍길동</ntceInsttOfclNm>
  <ntceInsttOfclTelNo>02-1234-5678</ntceInsttOfclTelNo>
  <ntceSpecDocUrl1>https://example.com/file1.pdf</ntceSpecDocUrl1>
  <ntceSpecFileNm1>첨부파일1.pdf</ntceSpecFileNm1>
  <ntceSpecDocUrl2>https://example.com/file2.hwp</ntceSpecDocUrl2>
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
        assert notice.region == ""  # v1.6.0 원칙 ② — 용역엔 지역 필드가 없다(수요기관명 dminsttNm은 extra)
        assert notice.extra["dminsttNm"] == "서울"

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
        # budget = 배정예산 asignBdgtAmt만(v1.6.0). 추정가격 presmptPrce는 extra 원문
        assert notice.budget == 60000000
        # 원문 extra(v1.2.5): 태그 이름 그대로, 값은 텍스트
        assert notice.extra["asignBdgtAmt"] == "60000000"
        assert notice.extra["presmptPrce"] == "50000000"

    def test_attachments_parsing(self):
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.attachments is not None
        assert len(notice.attachments) == 2
        assert notice.attachments[0]["name"] == "첨부파일1.pdf"
        assert notice.attachments[0]["url"] == "https://example.com/file1.pdf"
        assert notice.attachments[1] == {"name": "규격서2", "url": "https://example.com/file2.hwp"}  # 파일명 없으면 규격서{i}

    def test_category_service_is_large_class_only(self):
        """용역: 공공조달분류 대분류 한 필드(v1.6.0 원칙 ② — 종전 "대 > 중" 합성). 중분류는 extra 원문."""
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.category == "ICT 서비스"
        assert notice.extra["pubPrcrmntMidClsfcNm"] == "ICT사업 컨설팅"

    def test_category_no_fallback_to_other_class(self):
        """대분류가 없으면 중분류·세부품명으로 대체하지 않는다 → 빈 값."""
        xml = ("<item><bidNtceNo>3</bidNtceNo><bidNtceNm>t</bidNtceNm>"
               "<pubPrcrmntMidClsfcNm>중분류</pubPrcrmntMidClsfcNm><dtilPrdctClsfcNoNm>세부품명</dtilPrdctClsfcNoNm></item>")
        assert _item_to_notice(etree.fromstring(xml), "용역").category == ""

    def test_category_goods_uses_detail_product_name(self):
        """물품: 세부품명(dtilPrdctClsfcNoNm) — 실제 물품 목록 응답의 태그."""
        xml = (
            "<item><bidNtceNo>1</bidNtceNo><bidNtceNm>휴머노이드로봇 구매</bidNtceNm>"
            "<dtilPrdctClsfcNo>4898999903</dtilPrdctClsfcNo>"
            "<dtilPrdctClsfcNoNm>휴머노이드로봇</dtilPrdctClsfcNoNm></item>"
        )
        notice = _item_to_notice(etree.fromstring(xml), "물품")
        assert notice.category == "휴머노이드로봇"

    def test_category_construction_uses_main_work_type(self):
        """공사: 주공종(mainCnsttyNm) — 실제 공사 목록 응답의 태그."""
        xml = (
            "<item><bidNtceNo>2</bidNtceNo><bidNtceNm>맨홀추락방지시설 설치공사</bidNtceNm>"
            "<mainCnsttyNm>상ㆍ하수도설비공사업</mainCnsttyNm></item>"
        )
        notice = _item_to_notice(etree.fromstring(xml), "공사")
        assert notice.category == "상ㆍ하수도설비공사업"

    def test_extra_fields(self):
        """v1.2.5 원칙 ①: extra는 응답 태그 전부·원래 이름 — 영어 별칭·요청 문맥(bid_type)은 없다."""
        item = self._make_item()
        notice = _item_to_notice(item, "용역")
        assert notice.extra["bidMethdNm"] == "제한경쟁"
        assert notice.extra["cntrctMthdNm"] == "총액계약"
        assert notice.extra["ntceInsttOfclNm"] == "홍길동"
        assert notice.extra["ntceInsttOfclTelNo"] == "02-1234-5678"
        assert notice.extra["opengDt"] == "202604200900"
        assert notice.extra["bidNtceNo"] == "20260405001"  # 표준 필드로 옮긴 값도 원문 그대로
        assert "bid_type" not in notice.extra and "contact" not in notice.extra
        assert len(notice.extra) == len([el for el in item if el.text and el.text.strip()])

    def test_empty_fields_handled_gracefully(self):
        """최소한의 필드만 있는 item도 에러 없이 변환."""
        xml = "<item><bidNtceNo>99999</bidNtceNo><bidNtceNm>최소공고</bidNtceNm><ntceInsttNm>기관</ntceInsttNm></item>"
        item = etree.fromstring(xml)
        notice = _item_to_notice(item, "공사")
        assert notice.bid_no == "공사-99999"
        assert notice.title == "최소공고"
        assert notice.attachments is None
        assert notice.budget is None

    # --- v1.6.0 원칙 ② — 표준 필드는 원문 한 필드, 대체·추정 없음 ---
    def test_budget_not_replaced_by_estimated_price(self):
        """배정예산이 없으면 추정가격으로 대체하지 않는다 → None(추정가격은 extra)."""
        xml = ("<item><bidNtceNo>4</bidNtceNo><bidNtceNm>t</bidNtceNm>"
               "<presmptPrce>50000000</presmptPrce></item>")
        n = _item_to_notice(etree.fromstring(xml), "용역")
        assert n.budget is None
        assert n.extra["presmptPrce"] == "50000000"

    def test_construction_budget_is_bdgtAmt_and_region_is_site(self):
        """공사: 배정예산 태그 bdgtAmt(추정가격 아님), 지역 = 공사현장지역 cnstrtsiteRgnNm(수요기관명 아님)."""
        xml = ("<item><bidNtceNo>5</bidNtceNo><bidNtceNm>t</bidNtceNm><bdgtAmt>70000000</bdgtAmt>"
               "<presmptPrce>63636364</presmptPrce><cnstrtsiteRgnNm>경기도 수원시</cnstrtsiteRgnNm>"
               "<dminsttNm>수요기관</dminsttNm></item>")
        n = _item_to_notice(etree.fromstring(xml), "공사")
        assert n.budget == 70000000
        assert n.region == "경기도 수원시"

    def test_cancel_notice_is_cancelled(self):
        """출처가 명시한 취소(ntceKindNm=취소공고) → cancelled, 마감일이 미래여도."""
        xml = ("<item><bidNtceNo>6</bidNtceNo><bidNtceNm>t</bidNtceNm><ntceKindNm>취소공고</ntceKindNm>"
               "<bidClseDt>2099-12-31 10:00:00</bidClseDt></item>")
        assert _item_to_notice(etree.fromstring(xml), "용역").status == "cancelled"
        normal = xml.replace("취소공고", "등록공고")
        assert _item_to_notice(etree.fromstring(normal), "용역").status == "ongoing"

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
        notices, pages, errors = await collector._fetch(days=1, bid_types=["용역"], **kwargs)
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
        notices, pages, errors = await collector._fetch(days=1, bid_types=["용역"])
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
        notices, pages, errors = await collector._fetch(days=1, bid_types=["용역"])
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
            notices, pages, errors = await collector._fetch(days=1, bid_types=["용역"])

        assert call_count == 2
        assert len(notices) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_exhausts_retries(self):
        """429가 MAX_RETRIES까지 반복 → 빈 결과 + errors에 원인."""
        operation = BID_SERVICES["용역"]
        respx.get(f"{BASE_URL}/{operation}").mock(
            return_value=httpx.Response(429)
        )

        collector = NaraCollector(api_key="test-key")
        with patch("bid_collectors.nara.asyncio.sleep", new_callable=AsyncMock):
            notices, pages, errors = await collector._fetch(days=1, bid_types=["용역"])

        assert notices == []
        assert len(errors) == 1
        assert "429 재시도 3회 소진" in errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_quota_error_keeps_earlier_services(self):
        """물품 서비스의 resultCode 에러(쿼터 초과)가 앞서 수집한 용역 결과를 버리지 않는다 (F-001)."""
        quota = _make_xml_response(
            result_code="22", result_msg="LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR.", total_count=0
        )
        respx.get(f"{BASE_URL}/{BID_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=SAMPLE_RESPONSE)
        )
        goods = respx.get(f"{BASE_URL}/{BID_SERVICES['물품']}").mock(
            return_value=httpx.Response(200, content=quota)
        )

        result = await NaraCollector(api_key="test-key").collect(days=10, bid_types=["용역", "물품"])
        assert len(result.notices) == 1
        assert result.notices[0].bid_no.startswith("용역-")
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "물품" in result.errors[0]
        assert "22 - LIMITED_NUMBER" in result.errors[0]
        # 쿼터 에러 뒤 같은 서비스의 남은 기간은 요청하지 않는다 (days=10 → 기간 2개)
        assert goods.call_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_failure_masks_key(self):
        """재시도 소진 오류 메시지에 serviceKey가 새지 않는다."""
        secret = "Ab+c/D==SECRET"
        respx.get(f"{BASE_URL}/{BID_SERVICES['용역']}").mock(return_value=httpx.Response(500))

        with patch("bid_collectors.nara.asyncio.sleep", new_callable=AsyncMock):
            result = await NaraCollector(api_key=secret).collect(days=1, bid_types=["용역"])
        joined = " ".join(result.errors)
        assert len(result.errors) == 1
        assert "SECRET" not in joined
        assert "serviceKey=***" in joined


# ---------------------------------------------------------------------------
# 나라장터 확장 3메서드 (F-002) — 반환형 list[Notice], 실패는 예외
# ---------------------------------------------------------------------------

AWARD_ITEM_XML = """\
<item>
  <bidNtceNo>R26BK0001</bidNtceNo><bidNtceOrd>000</bidNtceOrd>
  <bidNtceNm>낙찰 테스트</bidNtceNm><dminsttNm>수요기관</dminsttNm>
  <fnlSucsfDate>2026-09-01</fnlSucsfDate><sucsfbidAmt>12345000</sucsfbidAmt>
  <bidwinnrNm>낙찰업체</bidwinnrNm><sucsfbidRate>87.5</sucsfbidRate><prtcptCnum>7</prtcptCnum>
</item>"""

# 공사 계약의 제목 태그는 cnstwkNm(cntrctNm 없음) — 2026-09-25 실측 61/61건. 종전 픽스처는 cntrctNm을 넣어 공사 전건 누락을 가렸다
CONTRACT_ITEM_XML = """\
<item>
  <dcsnCntrctNo>C26000111</dcsnCntrctNo><cnstwkNm>계약 테스트</cnstwkNm>
  <cntrctInsttNm>계약기관</cntrctInsttNm><thtmCntrctAmt>5000000</thtmCntrctAmt>
</item>"""

PRESPEC_ITEM_XML = """\
<item>
  <bfSpecRgstNo>P26000999</bfSpecRgstNo><prdctClsfcNoNm>사전규격 품명</prdctClsfcNoNm>
  <orderInsttNm>발주기관</orderInsttNm><opninRgstClseDt>2099-12-31 18:00</opninRgstClseDt>
  <specDocFileUrl1>https://example.com/s1.pdf</specDocFileUrl1>
  <specDocFileUrl2>https://example.com/s2.pdf</specDocFileUrl2>
</item>"""


class TestNaraExtended:
    @pytest.mark.asyncio
    @respx.mock
    async def test_pre_specs_mapping(self):
        from bid_collectors.nara import PRE_SPEC_BASE_URL, PRE_SPEC_SERVICES

        route = respx.get(f"{PRE_SPEC_BASE_URL}/{PRE_SPEC_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(PRESPEC_ITEM_XML))
        )
        notices = await NaraCollector(api_key="test-key").collect_pre_specs(days=1, bid_types=["용역"])
        assert len(notices) == 1
        n = notices[0]
        assert n.bid_no == "사전규격-용역-P26000999"
        assert str(n.end_date) == "2099-12-31"
        assert [a["url"] for a in n.attachments] == ["https://example.com/s1.pdf", "https://example.com/s2.pdf"]
        # 사전규격 API만 ServiceKey(대문자 S)
        assert "ServiceKey" in route.calls[0].request.url.params

    @pytest.mark.asyncio
    @respx.mock
    async def test_awards_mapping(self):
        from bid_collectors.nara import AWARD_BASE_URL, AWARD_SERVICES

        respx.get(f"{AWARD_BASE_URL}/{AWARD_SERVICES['물품']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(AWARD_ITEM_XML))
        )
        notices = await NaraCollector(api_key="test-key").collect_awards(days=1, bid_types=["물품"])
        assert len(notices) == 1
        n = notices[0]
        assert n.bid_no == "낙찰-물품-R26BK0001-000"
        assert n.status == "closed"
        assert n.budget is None  # v1.6.0 — 낙찰금액은 예산이 아니다(extra 원문)
        assert n.extra["sucsfbidAmt"] == "12345000"
        assert str(n.start_date) == "2026-09-01"
        assert n.extra["bidwinnrNm"] == "낙찰업체"  # v1.2.5 원문 extra — 태그 이름 그대로
        assert n.extra["sucsfbidRate"] == "87.5"
        assert n.extra["prtcptCnum"] == "7"
        assert "data_type" not in n.extra and "bid_type" not in n.extra

    @pytest.mark.asyncio
    @respx.mock
    async def test_contracts_mapping(self):
        from bid_collectors.nara import CONTRACT_BASE_URL, CONTRACT_SERVICES

        respx.get(f"{CONTRACT_BASE_URL}/{CONTRACT_SERVICES['공사']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(CONTRACT_ITEM_XML))
        )
        notices = await NaraCollector(api_key="test-key").collect_contracts(days=1, bid_types=["공사"])
        assert len(notices) == 1
        assert notices[0].bid_no == "계약-공사-C26000111"
        assert notices[0].title == "계약 테스트"
        assert notices[0].budget == 5000000

    @pytest.mark.asyncio
    @respx.mock
    async def test_extended_no_fallbacks(self):
        """v1.6.0 원칙 ② — 낙찰 시작일은 최종낙찰일만(실개찰일 폴백 없음) / 계약 금액은 금차만(총계약 폴백 없음),
        end_date 없음(계약기간은 마감일이 아니다), region 없음(관할 구분은 지역이 아니다)."""
        from bid_collectors.nara import AWARD_BASE_URL, AWARD_SERVICES, CONTRACT_BASE_URL, CONTRACT_SERVICES

        award = "<item><bidNtceNo>A1</bidNtceNo><bidNtceNm>n</bidNtceNm><rlOpengDt>2026-09-01 10:00:00</rlOpengDt></item>"
        contract = ("<item><dcsnCntrctNo>C1</dcsnCntrctNo><cntrctNm>n</cntrctNm><totCntrctAmt>900</totCntrctAmt>"
                    "<cntrctPrd>2026-09-01 ~ 2026-12-31</cntrctPrd><cntrctInsttJrsdctnDivNm>국가기관</cntrctInsttJrsdctnDivNm></item>")
        respx.get(f"{AWARD_BASE_URL}/{AWARD_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(award)))
        respx.get(f"{CONTRACT_BASE_URL}/{CONTRACT_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(contract)))
        c = NaraCollector(api_key="test-key")
        a = (await c.collect_awards(days=1, bid_types=["용역"]))[0]
        k = (await c.collect_contracts(days=1, bid_types=["용역"]))[0]
        assert a.start_date is None
        assert (k.budget, k.end_date, k.region) == (None, None, "")
        assert k.extra["cntrctPrd"] == "2026-09-01 ~ 2026-12-31"

    @pytest.mark.asyncio
    @respx.mock
    async def test_contracts_construction_all_items_kept(self, caplog):
        """v1.3.1 — 공사 N건(제목 cnstwkNm만) → N건 반환, 건너뜀 경고 없음. 용역·물품은 cntrctNm."""
        from bid_collectors.nara import CONTRACT_BASE_URL, CONTRACT_SERVICES

        cnstwk = "".join(f"<item><untyCntrctNo>R26TE{i}</untyCntrctNo><cnstwkNm>공사{i}</cnstwkNm></item>" for i in range(5))
        servc = "<item><untyCntrctNo>R26TE9</untyCntrctNo><cntrctNm>용역 계약</cntrctNm></item>"
        respx.get(f"{CONTRACT_BASE_URL}/{CONTRACT_SERVICES['공사']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(cnstwk, total_count=5)))
        respx.get(f"{CONTRACT_BASE_URL}/{CONTRACT_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(servc, total_count=1)))
        with caplog.at_level(logging.WARNING, logger="bid_collectors"):
            notices = await NaraCollector(api_key="test-key").collect_contracts(days=1, bid_types=["공사", "용역"])
        assert [n.title for n in notices] == [f"공사{i}" for i in range(5)] + ["용역 계약"]
        assert "건너뜀" not in caplog.text

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_error_propagates(self):
        from bid_collectors.nara import PRE_SPEC_BASE_URL, PRE_SPEC_SERVICES

        respx.get(f"{PRE_SPEC_BASE_URL}/{PRE_SPEC_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(result_code="30", result_msg="SERVICE_KEY_IS_NOT_REGISTERED_ERROR."))
        )
        with pytest.raises(ValueError, match="30 - SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
            await NaraCollector(api_key="test-key").collect_pre_specs(days=1, bid_types=["용역"])

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_failure_raises_masked(self):
        """재시도 소진은 조용한 빈 결과가 아니라 예외 — 메시지에 키가 없다."""
        from bid_collectors.nara import PRE_SPEC_BASE_URL, PRE_SPEC_SERVICES

        secret = "Ab+c/D==SECRET"
        respx.get(f"{PRE_SPEC_BASE_URL}/{PRE_SPEC_SERVICES['용역']}").mock(return_value=httpx.Response(500))
        with patch("bid_collectors.nara.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError) as exc:
                await NaraCollector(api_key=secret).collect_pre_specs(days=1, bid_types=["용역"])
        assert "SECRET" not in str(exc.value)
        assert "ServiceKey=***" in str(exc.value)
        assert exc.value.__suppress_context__ is True

    # v1.2.5 A — ID 없는 항목이 `낙찰-용역-`로 합쳐져 서로 다른 공고가 1건이 되던 결함. 반환형이 list라 보고는 경고 로그뿐(CONTRACT.md)
    @pytest.mark.asyncio
    @respx.mock
    async def test_awards_without_id_are_skipped_not_merged(self, caplog):
        from bid_collectors.nara import AWARD_BASE_URL, AWARD_SERVICES

        three = "<item><bidNtceNm>낙찰</bidNtceNm><dminsttNm>기관</dminsttNm></item>" * 3
        respx.get(f"{AWARD_BASE_URL}/{AWARD_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(three, total_count=3))
        )
        with caplog.at_level(logging.WARNING, logger="bid_collectors"):
            notices = await NaraCollector(api_key="test-key").collect_awards(days=1, bid_types=["용역"])
        assert notices == []
        assert "3건 건너뜀" in caplog.text and "bidNtceNo" in caplog.text

    @pytest.mark.asyncio
    @respx.mock
    async def test_contracts_without_id_are_skipped_not_merged(self, caplog):
        from bid_collectors.nara import CONTRACT_BASE_URL, CONTRACT_SERVICES

        three = "<item><cntrctNm>계약</cntrctNm><thtmCntrctAmt>1</thtmCntrctAmt></item>" * 3
        respx.get(f"{CONTRACT_BASE_URL}/{CONTRACT_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(three, total_count=3))
        )
        with caplog.at_level(logging.WARNING, logger="bid_collectors"):
            notices = await NaraCollector(api_key="test-key").collect_contracts(days=1, bid_types=["용역"])
        assert notices == []
        assert "3건 건너뜀" in caplog.text and "cntrctNo" in caplog.text

    @pytest.mark.asyncio
    @respx.mock
    async def test_pre_specs_without_id_are_skipped_not_merged(self, caplog):
        from bid_collectors.nara import PRE_SPEC_BASE_URL, PRE_SPEC_SERVICES

        three = "<item><prdctClsfcNoNm>품명</prdctClsfcNoNm><orderInsttNm>기관</orderInsttNm></item>" * 3
        respx.get(f"{PRE_SPEC_BASE_URL}/{PRE_SPEC_SERVICES['용역']}").mock(
            return_value=httpx.Response(200, content=_make_xml_response(three, total_count=3))
        )
        with caplog.at_level(logging.WARNING, logger="bid_collectors"):
            notices = await NaraCollector(api_key="test-key").collect_pre_specs(days=1, bid_types=["용역"])
        assert notices == []
        assert "3건 건너뜀" in caplog.text and "bfSpecRgstNo" in caplog.text


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
