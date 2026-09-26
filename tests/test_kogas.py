"""한국가스공사 입찰정보 수집기(kogas.py) 단위 테스트 — F-012."""

from datetime import date, timedelta

import httpx
import pytest
import respx
from lxml import etree

from bid_collectors import KogasCollector
from bid_collectors.kogas import API_URL, DETAIL_URL, _item_to_notice


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


# ── fetch_detail (v1.5.0) ── 실측 화면(2026-09-26 `scripts/_tmp/kogas_dtl/2026092205_001_01.html`)의 뼈대를 줄인 모양. EUC-KR로 온다.
DETAIL_HTML = """<html><body>
<table><tr><td class="st_c">공고중</td><td class="st_t">아래의 입찰이 취소되었습니다.</td></tr></table>
<table>
<tr><td class="t_g">공고번호</td><td class="c">2026092205</td><td class="t_g">긴급입찰</td><td class="c">긴급</td></tr>
<tr><td class="t_g">건명</td><td class="c">2026년 공급관리소 무정전전원장치
  구매</td></tr>
<tr><td class="t_g">업무구분</td><td class="c">내자<br>-물품<br>-전자입찰</td></tr>
<tr><td class="t_g">계약담당(공고등록,개찰)</td><td class="c">()</td></tr>
<tr><td class="t_g">비고</td><td class="c">&nbsp;</td></tr>
<tr><td class="t_g">면허사항제한</td><td class="c"><table><tr><td class="t_g">업종그룹1</td><td class="c">전기공사업</td></tr>
  <tr><td class="t_g">업종그룹2</td><td class="c">-</td></tr></table></td></tr>
<tr><td class="t_g">추정가격</td><td class="c">559,744,000</td><td class="t_g">지급금액</td><td class="c">0</td></tr>
</table>
<div id="itempanel" style="display:none"><table>
<tr><td class="t_c">품목순번</td><td class="t_c">내역</td><td class="t_c">요청수량</td><td class="t_c">단위</td><td class="t_c">추정단가</td></tr>
<tr><td class="c_c">00010</td><td class="c"><span title="양산관리소 UPS">양산관리소 UPS</span></td><td class="c r">1&nbsp;</td><td class="c_c">SET</td><td class="c r">105,600,000&nbsp;</td></tr>
<tr><td class="c">서비스</td><td class="c" colspan="2">UPS 설치</td><td class="c r">&nbsp;</td><td class="c_c">5,000</td></tr>
</table></div>
<table><tr><td class="s_title">필요첨부파일</td></tr></table>
<table id="tb_reqfile"><tr><td class="c"><a href="/supplier/bid/bid_download_rule_proc.jsp?rule_no=LDD0000013&ruleSeq=2">물품구매(제조)계약일반조건.zip</a></td></tr></table>
<table><tr><td class="s_title">기타첨부파일</td></tr></table>
<table id="tb_etcfile"><tr><td class="t_c">항목</td></tr>
<tr><td class="c"><a href="/supplier/bid/bid_download_attfile.jsp?notice_code=2026092205&seq=23">입찰공고문.hwp</a></td></tr>
<tr><td class="c"><a href="/supplier/bid/bid_download_attfile.jsp?notice_code=2026092205&seq=37">1. UPS 구매규격서.doc</a></td></tr>
<tr><td class="c"><a href="javascript:void(0)">도움말</a></td></tr></table>
</body></html>"""


def _detail_resp(html: str = DETAIL_HTML, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=html.encode("cp949"), headers={"Content-Type": "text/html; charset=euc-kr"})


class TestFetchDetail:
    @respx.mock
    async def test_detail_maps_items_and_attachments(self):
        route = respx.get(DETAIL_URL).mock(return_value=_detail_resp())
        d = await KogasCollector(api_key="k").fetch_detail("KOGAS-2026092205")
        assert dict(route.calls[0].request.url.params) == {"notice_code": "2026092205", "bid_code": "001", "round": "01"}
        assert d["건명"] == "2026년 공급관리소 무정전전원장치 구매"  # 공백·줄바꿈 정리
        assert d["업무구분"] == "내자 -물품 -전자입찰"
        assert d["계약담당(공고등록,개찰)"] == "()"  # 원문의 빈 표기는 정리하지 않는다
        assert "비고" not in d  # 빈 값은 뺀다
        assert d["면허사항제한"] == "업종그룹1 전기공사업 업종그룹2 -"  # 중첩 항목은 바깥 값에
        assert "업종그룹1" not in d
        assert d["지급금액"] == "0"  # 0은 값이다
        assert (d["진행상태"], d["진행안내"]) == ("공고중", "아래의 입찰이 취소되었습니다.")
        assert d["품목내역"] == [
            {"품목순번": "00010", "내역": "양산관리소 UPS", "요청수량": "1", "단위": "SET", "추정단가": "105,600,000"},
            {"품목순번": "서비스", "내역": "UPS 설치", "추정단가": "5,000"},  # colspan 하위 행 — 병합 칸을 펼쳐 열을 맞춘다
        ]
        assert d["content"] == ""
        assert d["attachments"] == [  # 내려받기 링크 전부·페이지 순서, 절대 주소
            {"name": "물품구매(제조)계약일반조건.zip",
             "url": "https://bid.kogas.or.kr:9443/supplier/bid/bid_download_rule_proc.jsp?rule_no=LDD0000013&ruleSeq=2"},
            {"name": "입찰공고문.hwp",
             "url": "https://bid.kogas.or.kr:9443/supplier/bid/bid_download_attfile.jsp?notice_code=2026092205&seq=23"},
            {"name": "1. UPS 구매규격서.doc",
             "url": "https://bid.kogas.or.kr:9443/supplier/bid/bid_download_attfile.jsp?notice_code=2026092205&seq=37"},
        ]

    @respx.mock
    async def test_detail_not_found_raises(self):
        """없는 공고: HTTP 200 + alert만 든 159바이트(2026-09-26 실측) — 빈 dict로 넘기지 않는다."""
        respx.get(DETAIL_URL).mock(return_value=_detail_resp(
            '<script>alert("정보가 존재하지 않습니다. 다시 확인하여 주십시오.");top.location.href="/";</script>'))
        with pytest.raises(ValueError, match="없는 공고"):
            await KogasCollector(api_key="k").fetch_detail("KOGAS-9999999999")

    @pytest.mark.parametrize("html", [
        DETAIL_HTML.replace(">2026092205</td>", ">2026092206</td>"),  # 다른 공고가 옴
        DETAIL_HTML.replace('class="t_g">건명', 'class="t_x">건명'),     # 화면 개편 — 건명 칸을 못 찾음
        "<html><body>점검 중입니다</body></html>",
        # 첨부가 조용히 []가 되는 개편 — 링크 경로가 바뀜 / 첨부 절 제목이 바뀜
        DETAIL_HTML.replace("/supplier/bid/bid_download_attfile.jsp", "/supplier/file/get.jsp"),
        DETAIL_HTML.replace("필요첨부파일", "필요서류").replace("기타첨부파일", "기타서류"),
    ])
    @respx.mock
    async def test_detail_layout_change_raises(self, html):
        respx.get(DETAIL_URL).mock(return_value=_detail_resp(html))
        with pytest.raises(ValueError, match="화면 개편 의심"):
            await KogasCollector(api_key="k").fetch_detail("KOGAS-2026092205")

    @respx.mock
    async def test_http_error_raises(self):
        """bid_code·round가 001/01이 아니면 400(실측) — 예외로."""
        respx.get(DETAIL_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(httpx.HTTPStatusError):
            await KogasCollector(api_key="k").fetch_detail("KOGAS-2026092205")

    @pytest.mark.parametrize("bad", ["2026092205", "KOGAS-", "LH-1"])
    @respx.mock
    async def test_bad_bid_no_raises_without_request(self, bad):
        route = respx.get(DETAIL_URL)
        with pytest.raises(ValueError, match="bid_no 형식"):
            await KogasCollector(api_key="k").fetch_detail(bad)
        assert route.call_count == 0
