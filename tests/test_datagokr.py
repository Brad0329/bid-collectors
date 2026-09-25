"""utils/datagokr.py — 기관 수집기 4종이 공유하는 data.go.kr XML 파싱·페이지 루프 (v1.4.0)."""

import httpx
import pytest
import respx

from bid_collectors.utils.datagokr import fetch_pages, parse_xml

URL = "https://apis.data.go.kr/test/list"


def _xml(n: int, total: int, code: str = "00") -> bytes:
    items = "".join(f"<item><id>{i}</id></item>" for i in range(n))
    return (f"<response><header><resultCode>{code}</resultCode><resultMsg>MSG</resultMsg></header>"
            f"<body><items>{items}</items><totalCount>{total}</totalCount></body></response>").encode()


class TestParseXml:
    def test_ok(self):
        items, total = parse_xml(_xml(2, 7))
        assert len(items) == 2
        assert total == 7

    def test_error_code_raises_with_code_and_message(self):
        with pytest.raises(ValueError, match="API 에러: 11 - MSG"):
            parse_xml(_xml(0, 0, code="11"))

    def test_nodata_code_is_empty(self):
        assert parse_xml(_xml(0, 0, code="03"), nodata_codes=("03",)) == ([], 0)

    def test_nodata_code_not_listed_raises(self):
        with pytest.raises(ValueError, match="03"):
            parse_xml(_xml(0, 0, code="03"))

    @pytest.mark.parametrize("body", [b"", b"   \n"])
    def test_empty_body_is_failure_not_zero(self, body):
        """수자원 실측: 약 51KB 초과 응답은 HTTP 200 + 본문 0바이트 — 0건으로 삼키면 조용한 실패다."""
        with pytest.raises(ValueError, match="빈 응답 본문"):
            parse_xml(body)

    def test_gateway_error(self):
        body = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
                "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
                "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>").encode()
        with pytest.raises(ValueError, match="returnReasonCode=30 - SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
            parse_xml(body)

    def test_euc_kr_declaration_bytes(self):
        body = ('<?xml version="1.0" encoding="EUC-KR"?><response><header><resultCode>00</resultCode></header>'
                "<body><items><item><t>부산울산지역본부 사옥</t></item></items><totalCount>1</totalCount></body></response>"
                ).encode("euc-kr")
        items, _ = parse_xml(body)
        assert items[0].findtext("t") == "부산울산지역본부 사옥"


async def _run(max_pages: int = 10, rows: int = 2):
    async with httpx.AsyncClient() as client:
        return await fetch_pages(client, URL, {"serviceKey": "SECRETKEY"}, parse_xml,
                                 rows=rows, max_pages=max_pages, label="[T]",
                                 mask=lambda s: s.replace("SECRETKEY", "***"))


class TestFetchPages:
    @respx.mock
    async def test_paginates_until_total(self):
        route = respx.get(URL).mock(side_effect=[
            httpx.Response(200, content=_xml(2, 5)),
            httpx.Response(200, content=_xml(2, 5)),
            httpx.Response(200, content=_xml(1, 5)),
        ])
        items, pages, errors = await _run()
        assert (len(items), pages, errors) == (5, 3, [])
        assert route.call_count == 3
        assert [c.request.url.params["pageNo"] for c in route.calls] == ["1", "2", "3"]

    @respx.mock
    async def test_second_page_failure_keeps_first(self):
        respx.get(URL).mock(side_effect=[
            httpx.Response(200, content=_xml(2, 5)),
            httpx.Response(500),
        ])
        items, pages, errors = await _run()
        assert (len(items), pages) == (2, 1)
        assert len(errors) == 1
        assert "[T] 페이지 2 요청 실패: HTTPStatusError" in errors[0]
        assert "SECRETKEY" not in errors[0]

    @respx.mock
    async def test_empty_middle_page_with_total_left_is_reported(self):
        """totalCount가 남았는데 빈 페이지가 오면 조용히 멈추지 않고 받은 건수와 전체 건수를 알린다(spec-checker 2026-09-26)."""
        respx.get(URL).mock(side_effect=[
            httpx.Response(200, content=_xml(2, 5)),
            httpx.Response(200, content=_xml(0, 5)),
        ])
        items, pages, errors = await _run()
        assert (len(items), pages) == (2, 1)
        assert errors == ["[T] 페이지 2가 비었음 — 전체 5건 중 2건만 받음"]

    @respx.mock
    async def test_empty_first_page_with_zero_total_is_not_error(self):
        respx.get(URL).mock(return_value=httpx.Response(200, content=_xml(0, 0)))
        assert await _run() == ([], 0, [])

    @respx.mock
    async def test_empty_body_page_is_reported(self):
        respx.get(URL).mock(return_value=httpx.Response(200, content=b""))
        items, pages, errors = await _run()
        assert (items, pages) == ([], 0)
        assert "빈 응답 본문" in errors[0]

    @respx.mock
    async def test_max_pages_truncation_reported_with_total(self):
        respx.get(URL).mock(return_value=httpx.Response(200, content=_xml(2, 9)))
        items, pages, errors = await _run(max_pages=2)
        assert (len(items), pages) == (4, 2)
        assert errors == ["[T] max_pages=2 상한 도달로 중단 — 전체 9건 중 4건까지만 조회"]
