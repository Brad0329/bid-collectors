"""LH 입찰공고 수집기(lh.py) 단위 테스트 — F-011."""

import ssl
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from lxml import etree

from bid_collectors import LhCollector
from bid_collectors.lh import (API_URL, DOWNLOAD_URL, LIST_URL, SEARCH_URL, SITE, _item_to_notice, detail_url,
                               lh_ssl_context)


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
        assert "BidgdsDetailListCmd" in detail_url("물품", "1", "00")  # 2026-09-26 확인(검색 화면 JS 업무 코드 30)
        assert detail_url("모르는업무", "1", "00") == LIST_URL  # 깨진 링크 대신 첫 화면


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


# ── fetch_detail (v1.5.0) ── 실측 화면(2026-09-26 `scripts/_tmp/lh_dtl/`)의 뼈대를 줄인 모양
SEARCH_HTML = """<script>function fn_dds_open(bidNum, bidDegree, cstrtnJobGbCd, emrgncyOrder) {}</script>
<table><tr class="Lfirst" onclick="fn_dds_open('2602491', '01', '10', 'Y');" onKeyPress="fn_dds_open('Y');">
<td>2602491</td><td>시설공사</td></tr></table>"""
SEARCH_EMPTY = "<script>function fn_dds_open(bidNum, bidDegree, cstrtnJobGbCd, emrgncyOrder) {}</script><table></table>"
DETAIL_HTML = """<html><head><script>
function fn_dds_open(bidAttachdocType, bidAttachfileNm, svrBidFilePath, clntBidFilePath) {}
</script></head><body>
<table summary="공고일반정보">
 <tr><th>입찰공고번호</th><td>2602491 - 01</td><th>공고종류</th><td>정정공고 (낙찰)</td></tr>
 <tr><th>입찰공고건명</th><td>남양주별내 A-25BL
   경계석 보수공사</td></tr>
 <tr><th>추정가격</th><td>236,409,285원 (이억삼천육백사십만구천이백팔십오원)</td></tr>
 <tr><th>비고</th><td></td></tr>
</table>
<table summary="투찰제한정보"><tr><th>참가지역1</th><td>경기</td><th>참가지역2</th><td></td></tr></table>
<table summary="지역의무공동업체제한"><tr><th>참가지역1</th><td>서울</td></tr></table>
<table summary="요구면허"><thead><tr><th>번호</th><th>업종</th><th>요구면허2</th></tr></thead>
 <tbody><tr><td>1</td><td>지반조성,포장공사업</td><td></td></tr></tbody></table>
<table summary="요구면허"><tr><th>번호</th><th>면허구분</th></tr><tr><td>1</td><td>주력</td></tr></table>
<table summary="파일정보"><tr><th>문서명</th><th>공고파일명</th></tr>
 <tr><td>현장설명서</td><td><a href="#" onclick="fn_dds_open('70','2. 현장설명서(별내 A-25BL)_20260708.hwp', '/attachEBID/bidinfo/2. 현장설명서(별내 A-25BL)_20260708.hwp','2. 현장설명서(별내 A-25BL).hwp')">2. 현장설명서(별내 A-25BL).hwp</a></td></tr>
 <tr><td>입찰공고</td><td><a href="#" onclick="fn_dds_open('10','공고문_2026.hwp', '/attachEBID/bidinfo/공고문_2026.hwp','공고문.hwp')">공고문.hwp</a></td></tr>
</table>
<table summary="공고변경정보"><thead><tr><th>공고차수</th><th>공고구분</th><th>진행상태</th></tr></thead>
 <tbody><tr class="Lfirst" onclick="fn_open_histroy('2602491','00')" style="cursor:hand"/>
   <td>00</td><td>일반공고</td><td>정정처리</td></tr></tbody></table>
</body></html>"""
DETAIL_CONSTRUCT = SITE + "ebid.et.tp.cmd.BidConstructDetailListCmd.dev"


def _mock_detail(search: str = SEARCH_HTML, detail: str = DETAIL_HTML):
    s = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, text=search))
    d = respx.get(DETAIL_CONSTRUCT).mock(return_value=httpx.Response(200, text=detail))
    return s, d


class TestFetchDetail:
    @respx.mock
    async def test_detail_uses_latest_degree_and_job_cmd(self):
        search, detail = _mock_detail()
        await LhCollector(api_key="k").fetch_detail("LH-2602491")
        # 날짜 칸을 비워 보낸다(빼면 서버 기본 기간이 걸려 지난 공고 0건)
        assert parse_qs(search.calls[0].request.content.decode(), keep_blank_values=True) == {
            "s_bidNum": ["2602491"], "s_tndrdocAcptOpenDtm": [""], "s_tndrdocAcptEndDtm": [""]}
        assert dict(detail.calls[0].request.url.params) == {"bidNum": "2602491", "bidDegree": "01"}  # 업무 10 → 시설공사 경로

    @pytest.mark.parametrize("code, cmd", [("20", "BidsrvcsDetailListCmd"), ("30", "BidgdsDetailListCmd"),
                                           ("40", "BidctrctgdsDetailListCmd")])
    @respx.mock
    async def test_job_code_to_cmd(self, code, cmd):
        respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, text=SEARCH_HTML.replace("'10', 'Y'", f"'{code}', 'Y'")))
        route = respx.get(f"{SITE}ebid.et.tp.cmd.{cmd}.dev").mock(return_value=httpx.Response(200, text=DETAIL_HTML))
        await LhCollector(api_key="k").fetch_detail("LH-2602491")
        assert route.call_count == 1

    @respx.mock
    async def test_detail_maps_tables_and_attachments(self):
        _mock_detail()
        d = await LhCollector(api_key="k").fetch_detail("LH-2602491")
        assert d["공고일반정보/입찰공고건명"] == "남양주별내 A-25BL 경계석 보수공사"
        assert d["공고일반정보/추정가격"] == "236,409,285원 (이억삼천육백사십만구천이백팔십오원)"  # 원문 그대로
        assert "공고일반정보/비고" not in d
        # 같은 항목명이 두 표에 — 표 이름으로 구분
        assert (d["투찰제한정보/참가지역1"], d["지역의무공동업체제한/참가지역1"]) == ("경기", "서울")
        assert d["요구면허"] == [{"번호": "1", "업종": "지반조성,포장공사업"}]
        assert d["요구면허#2"] == [{"번호": "1", "면허구분": "주력"}]  # 같은 이름의 표가 다시 나오면 #2
        assert d["공고변경정보"] == [{"공고차수": "00", "공고구분": "일반공고", "진행상태": "정정처리"}]  # <tr .../> 깨진 행
        assert d["content"] == ""
        assert [a["name"] for a in d["attachments"]] == ["2. 현장설명서(별내 A-25BL).hwp", "공고문.hwp"]
        url = httpx.URL(d["attachments"][0]["url"])
        assert str(url).startswith(DOWNLOAD_URL)
        assert dict(url.params) == {"download.filespec": "bidinfo",
                                    "download.filename": "2.현장설명서(별내A-25BL).hwp",  # 사이트 JS처럼 공백 제거
                                    "download.savedname": "2. 현장설명서(별내 A-25BL)_20260708.hwp"}

    @pytest.mark.parametrize("search_html", [SEARCH_EMPTY, SEARCH_HTML.replace("'2602491', '01'", "'2602492', '01'")])
    @respx.mock
    async def test_detail_not_found_raises(self, search_html):
        """검색 0건, 또는 다른 번호의 행만 있음 — 다른 공고의 상세를 부르지 않는다."""
        search, detail = _mock_detail(search=search_html)
        with pytest.raises(ValueError, match="검색 결과 없음"):
            await LhCollector(api_key="k").fetch_detail("LH-9999999")
        assert detail.call_count == 0

    @pytest.mark.parametrize("html", [
        DETAIL_HTML.replace("2602491 - 01", "-"),                        # 없는 번호·틀린 차수: 같은 틀에 번호 칸 "-"
        DETAIL_HTML.replace("2602491 - 01", "2602491 - 00"),             # 다른 차수가 옴
        DETAIL_HTML.replace("남양주별내 A-25BL\n   경계석 보수공사", ""),     # 건명 빈 값
        DETAIL_HTML.replace('summary="공고일반정보"', 'summary="기본정보"'),  # 화면 개편
        DETAIL_HTML.replace("'공고문.hwp')", "'공고's.hwp')"),             # 첨부 링크를 다 못 읽음
        DETAIL_HTML.replace('summary="파일정보"', 'summary="첨부파일"'),    # 첨부가 조용히 []가 되는 개편
    ])
    @respx.mock
    async def test_detail_layout_change_raises(self, html):
        _mock_detail(detail=html)
        with pytest.raises(ValueError, match="LH 상세 LH-2602491"):
            await LhCollector(api_key="k").fetch_detail("LH-2602491")

    @respx.mock
    async def test_http_error_raises(self):
        respx.post(SEARCH_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await LhCollector(api_key="k").fetch_detail("LH-2602491")

    @pytest.mark.parametrize("bad", ["2602491", "LH-", "LH-26024a1", "KOGAS-1"])
    @respx.mock
    async def test_bad_bid_no_raises_without_request(self, bad):
        route = respx.route(url__startswith=SITE)
        with pytest.raises(ValueError, match="bid_no 형식"):
            await LhCollector(api_key="k").fetch_detail(bad)
        assert route.call_count == 0

    def test_lh_ssl_context(self):
        """ebid.lh.or.kr은 중간 인증서를 보내지 않는다 — 동봉한 것을 더하되 검증은 끈 적이 없어야 하고, 동봉 인증서는 만료 전이어야 한다."""
        ctx = lh_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
        mine = [c for c in ctx.get_ca_certs() if ("commonName", "TuringSign RSA Secure CA 2") in sum(c["subject"], ())]
        assert len(mine) == 1
        expires = datetime.strptime(mine[0]["notAfter"], "%b %d %H:%M:%S %Y %Z")
        assert expires > datetime.now() + timedelta(days=90), f"LH 중간 인증서 만료 임박({expires}) — 교체 필요"
