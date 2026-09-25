"""LH 입찰공고 수집기(lh.py) 단위 테스트 — F-011."""

from datetime import date, timedelta

import httpx
import respx
from lxml import etree

from bid_collectors import LhCollector
from bid_collectors.lh import API_URL, LIST_URL, _item_to_notice, detail_url


def _d(days: int, fmt: str = "%Y%m%d") -> str:
    return (date.today() + timedelta(days=days)).strftime(fmt)


def _item_xml(bid_num: str = "2603329", job: str = "시설공사", title: str = "부산울산지역본부 사옥 카리프트 교체공사") -> str:
    # 실측 원문 모양(2026-09-25): 값 뒤 공백, CDATA 제목, 빈 칸 태그
    return (f"<item><bidNum>{bid_num}</bidNum><bidDegree>01</bidDegree><cstrtnJobGbNm>{job} </cstrtnJobGbNm>"
            f"<bidKind>정정공고 </bidKind><bidnmKor><![CDATA[{title}]]> </bidnmKor><bidnmEng>  </bidnmEng>"
            f"<zoneHqCd>부산울산지역본부 </zoneHqCd><tndrCtrctMedCd>제한경쟁 </tndrCtrctMedCd>"
            f"<tndrbidRegDt>{_d(0)} </tndrbidRegDt><presmtPrc>134376962 </presmtPrc><designPrc>147814658 </designPrc>"
            f"<fdmtlAmt>147815000 </fdmtlAmt><tndrdocAcptEndDtm>{_d(5, '%Y/%m/%d')} 10:00 </tndrdocAcptEndDtm>"
            f"<zoneRstrct1>부산 </zoneRstrct1><zoneRstrct2>  </zoneRstrct2></item>")


def _body(items: list[str], total: int | None = None, code: str = "00") -> bytes:
    total = len(items) if total is None else total
    return ('<?xml version="1.0" encoding="EUC-KR"?>\n\n\t<response><header><resultCode>' + code +
            "</resultCode><resultMsg>MSG</resultMsg></header><body>" + "".join(items) +
            f"<numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>{total}</totalCount></body></response>"
            ).encode("euc-kr")


def _element(xml: str):
    return etree.fromstring(xml.encode())


class TestMapping:
    def test_item_maps_to_notice(self):
        n = _item_to_notice(_element(_item_xml()))
        assert n.source == "LH"
        assert n.bid_no == "LH-2603329"  # 차수 제외 — 정정은 같은 행의 bidDegree가 오른다
        assert n.title == "부산울산지역본부 사옥 카리프트 교체공사"
        assert n.organization == "한국토지주택공사"
        assert n.start_date == date.today()
        assert n.end_date == date.today() + timedelta(days=5)
        assert n.status == "ongoing"
        assert n.category == "시설공사"
        assert n.budget is None  # 금액은 extra 원문(2026-09-26 사용자)
        assert n.url == ("https://ebid.lh.or.kr/ebid.et.tp.cmd.BidConstructDetailListCmd.dev"
                         "?bidNum=2603329&bidDegree=01")
        assert n.extra["presmtPrc"] == "134376962"
        assert n.extra["designPrc"] == "147814658"
        assert n.extra["zoneHqCd"] == "부산울산지역본부"
        assert "bidnmEng" not in n.extra and "zoneRstrct2" not in n.extra  # 공백만인 칸은 뺀다

    def test_detail_url_by_job_type(self):
        assert "BidConstructDetailListCmd" in detail_url("시설공사", "1", "00")
        assert "BidsrvcsDetailListCmd" in detail_url("용역", "1", "00")
        assert "BidctrctgdsDetailListCmd" in detail_url("지급자재", "1", "00")
        assert detail_url("물품", "1", "00") == LIST_URL  # 경로 표본이 없다 — 깨진 링크 대신 첫 화면


class TestFetch:
    @respx.mock
    async def test_collect_sends_date_range_and_parses(self):
        route = respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([_item_xml("1"), _item_xml("2")])))
        result = await LhCollector(api_key="k").collect(days=7)
        assert [n.bid_no for n in result.notices] == ["LH-1", "LH-2"]
        assert result.errors == []
        params = route.calls[0].request.url.params
        assert params["tndrbidRegDtStart"] == _d(-7)
        assert params["tndrbidRegDtEnd"] == _d(0)

    @respx.mock
    async def test_euc_kr_response(self):
        respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([_item_xml(title="한글 제목 공사")])))
        result = await LhCollector(api_key="k").collect(days=7)
        assert result.notices[0].title == "한글 제목 공사"

    @respx.mock
    async def test_nodata_is_empty_not_error(self):
        respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([], total=0, code="03")))
        result = await LhCollector(api_key="k").collect(days=1)
        assert result.notices == []
        assert result.errors == []
        assert result.is_partial is False

    @respx.mock
    async def test_error_code_is_reported(self):
        respx.get(API_URL).mock(return_value=httpx.Response(200, content=_body([], total=0, code="11")))
        result = await LhCollector(api_key="k").collect(days=1)
        assert result.is_partial is True
        assert "API 에러: 11" in result.errors[0]

    @respx.mock
    async def test_first_page_failure_is_reported_and_key_masked(self):
        respx.get(API_URL).mock(return_value=httpx.Response(500))
        result = await LhCollector(api_key="SECRETKEY123").collect(days=1)
        assert result.notices == []
        assert "[LH] 페이지 1 요청 실패" in result.errors[0]
        assert "SECRETKEY123" not in result.errors[0]

    @respx.mock
    async def test_paginates_by_total_count(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, content=_body([_item_xml("1")], total=1001)),
            httpx.Response(200, content=_body([_item_xml("2")], total=1001)),
        ])
        result = await LhCollector(api_key="k").collect(days=7)
        assert route.call_count == 2
        assert len(result.notices) == 2
