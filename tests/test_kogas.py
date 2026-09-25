"""한국가스공사 입찰정보 수집기(kogas.py) 단위 테스트 — F-012."""

from datetime import date, timedelta

import httpx
import respx
from lxml import etree

from bid_collectors import KogasCollector
from bid_collectors.kogas import API_URL, _item_to_notice


def _d(days: int, fmt: str = "%Y-%m-%d") -> str:
    return (date.today() + timedelta(days=days)).strftime(fmt)


def _item_xml(code: str = "2026092314", title: str = "[평택] 스팀보일러 연수기 전용 재생용 소금 구매", cancel: str = "") -> str:
    # 실측 원문 모양(2026-09-25): 낙찰 필드는 빈 태그로 온다
    return (f"<item><NOTICE_CODE>{code}</NOTICE_CODE><NOTICE_NAME>{title}</NOTICE_NAME>"
            f"<BID_TYPE_NAME>전자입찰</BID_TYPE_NAME><WORK_TYPE_NAME>물품</WORK_TYPE_NAME>"
            f"<CONT_METHOD_NAME>일반경쟁</CONT_METHOD_NAME><NOTICE_DT>{_d(0)}</NOTICE_DT>"
            f"<END_DT>{_d(7)} 10:00</END_DT><OPEN_DT>{_d(7)} 10:10</OPEN_DT><CANCEL_YN>{cancel}</CANCEL_YN>"
            f"<SUCCESS_CORP_NAME></SUCCESS_CORP_NAME><SUCCESS_AMT></SUCCESS_AMT></item>")


def _body(items: list[str], total: int | None = None, code: str = "00") -> bytes:
    total = len(items) if total is None else total
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<response><header><resultCode>' + code +
            "</resultCode><resultMsg>MSG</resultMsg></header><body><items>" + "".join(items) +
            f"</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>{total}</totalCount></body></response>"
            ).encode()


class TestMapping:
    def test_item_maps_to_notice(self):
        n = _item_to_notice(etree.fromstring(_item_xml(cancel="취소").encode()))
        assert n.source == "가스공사"
        assert n.bid_no == "KOGAS-2026092314"
        assert n.title == "[평택] 스팀보일러 연수기 전용 재생용 소금 구매"
        assert n.organization == "한국가스공사"
        assert n.start_date == date.today()
        assert n.end_date == date.today() + timedelta(days=7)
        assert n.category == "물품"
        assert n.budget is None
        assert n.url == ("https://bid.kogas.or.kr:9443/supplier/contents/bid/bid_detail_view_notice.jsp"
                         "?notice_code=2026092314&bid_code=001&round=01")
        assert n.extra["CANCEL_YN"] == "취소"  # 취소 표시는 원문으로 넘긴다
        assert n.extra["CONT_METHOD_NAME"] == "일반경쟁"
        assert "SUCCESS_AMT" not in n.extra


class TestFetch:
    @respx.mock
    async def test_request_has_date_range(self):
        """날짜를 빼면 에러 없이 00 + 0건이 온다(실측) — 두 날짜가 늘 요청에 들어가야 한다."""
        route = respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([_item_xml()])))
        result = await KogasCollector(api_key="k").collect(days=14)
        assert len(result.notices) == 1
        params = route.calls[0].request.url.params
        assert params["DOCDATE_START"] == _d(-14, "%Y%m%d")
        assert params["DOCDATE_END"] == _d(0, "%Y%m%d")

    @respx.mock
    async def test_error_code_is_reported(self):
        respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([], total=0, code="30")))
        result = await KogasCollector(api_key="k").collect(days=1)
        assert result.is_partial is True
        assert "API 에러: 30" in result.errors[0]

    @respx.mock
    async def test_first_page_failure_is_reported_and_key_masked(self):
        respx.get(API_URL).mock(return_value=httpx.Response(500))
        result = await KogasCollector(api_key="SECRETKEY123").collect(days=1)
        assert result.notices == []
        assert "[가스공사] 페이지 1 요청 실패" in result.errors[0]
        assert "SECRETKEY123" not in result.errors[0]

    @respx.mock
    async def test_paginates_by_total_count(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, content=_body([_item_xml("1")], total=1001)),
            httpx.Response(200, content=_body([_item_xml("2")], total=1001)),
        ])
        result = await KogasCollector(api_key="k").collect(days=14)
        assert route.call_count == 2
        assert [n.bid_no for n in result.notices] == ["KOGAS-1", "KOGAS-2"]
