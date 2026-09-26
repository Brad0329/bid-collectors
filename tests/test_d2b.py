"""국방전자조달(d2b) 수집기(d2b.py) 단위 테스트 — F-013."""

from datetime import date, timedelta

import httpx
import pytest
import respx
from lxml import etree

from bid_collectors import D2bCollector
from bid_collectors.d2b import BASE_URL, DETAILS, LISTS, _item_to_notice

SPEC = {s.kind: s for s in LISTS}


def _d(days: int, fmt: str = "%Y%m%d") -> str:
    return (date.today() + timedelta(days=days)).strftime(fmt)


# 목록별 실측 원문 모양(2026-09-25 d2b_raw) — 값만 실행 시점 상대 날짜로. 상세 화면 주소 필드(orntCode·pblancSeCode·
# pblancYear·opengDt·purchsRequstNo·cntrctMth)는 v1.6.0 url용 — 실측 418행 전부 결측 0(scripts/_tmp/d2b_url/)
ITEMS = {
    "국내경쟁": {"bidNm": "TICN용 2차전지 모듈", "g2bPblancNo": "2026ERA00055606N", "g2bPblancOdr": "03",
              "pblancNo": "ERA0005", "pblancOdr": "3", "dcsNo": "5606N", "demandYear": "2026", "ornt": "우주지휘통신총괄계약팀",
              "busiDivs": "물품", "pblancDate": _d(0), "biddocPresentnClosDt": _d(3) + "1000", "bsicExpt": "17757000",
              "orntCode": "ERA", "pblancSeCode": "B", "cntrctMth": "제한경쟁", "pblancSe": "정상공고"},
    "국외경쟁": {"bsnsNm": "K5방독면 기밀시험장비", "g2bPblancNo": "2026ELA0026BBAL6013005", "g2bPblancOdr": "01",
              "pblancNo": "ELA0026", "pblancOdr": "1", "dcsNo": "BBAL6013", "groupNo": "005", "busiDivs": "용역",
              "bidRegistClosDt": _d(10) + "1400", "pblancCanclAt": "N", "pblancYear": "2026",
              "opengDt": _d(11) + "1000", "purchsRequstNo": "L26000123", "cntrctMth": "2단계경쟁(동시)"},
    "시설경쟁": {"cntrwkNm": "00부대 사무실 환경 개선공사", "g2bPblancNo": "2026LGP01272026-15117", "g2bPblancOdr": "01",
              "pblancOdr": "1", "cntrwkNo": "2026-15117", "ornt": "제1군수지원사령부", "busiDivs": "공사", "pblancDate": _d(0),
              "biddocPresentnClosDt": _d(3) + "1000", "baseAmnt": "268161450", "pblancNo": "LGP0127", "orntCode": "LGP",
              "pblancSeCode": "A", "pblancYear": "2026", "cntrctMth": "일반경쟁"},
    "국내수의": {"othbcNtatNm": "시험세트 서보용 외주정비", "demandYear": "2026", "pblancNo": "HCF0191", "dcsNo": "35442",
              "pblancOdr": "2", "ornt": "공군군수사령부", "busiDivs": "용역", "ntatPlanDate": _d(0),
              "prqudoPresentnClosDt": _d(2) + "1000", "budgetAmount": "43120000", "iemNo": "***", "orntCode": "HCF",
              "progrsSttus": "진행중"},
    "시설수의": {"cntrwkNm": "조사본부 야외데크 보수공사", "pblancNo": "MCN0044", "cntrwkNo": "2026-14402", "pblancOdr": "1",
              "ornt": "국방부근무지원단", "busiDivs": "공사", "ntatPlanDate": _d(0), "prqudoPresentnClosDt": _d(2) + "1000",
              "orntCode": "MCN"},
}
# 구분별 상세 화면 주소(요청서 §2 틀 + 메뉴 파라미터) — 사이트 JS가 넘기는 값과 같은지 필드별로 대조한다
DETAIL_PAGE = {
    "국내경쟁": ("/pdb/bid/bidAnnounceView.do", {"lv2Divs": "1", "pageDivs": "G1", "bid_divs": "bid", "dprt_code": "ERA",
             "anmt_divs": "B", "anmt_numb": "ERA0005", "rqst_degr": "3", "dcsn_numb": "5606N", "rqst_year": "2026"}),
    "시설경쟁": ("/peb/bid/announceView.do", {"lv2Divs": "1", "pageDivs": "E1", "dprt_code": "LGP", "anmt_divs": "A",
             "anmt_numb": "LGP0127", "rqst_degr": "1", "dcsn_numb": "2026-15117", "rqst_year": "2026"}),
    "국외경쟁": ("/pcb/bid/bidAnnounceView.do", {"menuOption": "1", "grd_anmtYear": "2026", "grd_bidxDate": _d(11),
             "grd_anmtNumb": "ELA0026", "grd_dgNumb": "L26000123", "grd_anmtRqst": "1", "grd_dprtCode": "ELA",
             "grd_dcsnNumb": "BBAL6013", "grd_gropNumb": "005"}),
    "국내수의": ("/pdb/openNego/openNegoPlanView.do", {"pageDivs": "G", "dmst_itnb": "***", "dcsn_numb": "35442",
             "negn_pldt": _d(0), "negn_degr": "2", "dprt_code": "HCF", "ordr_year": "2026", "anmt_numb": "HCF0191"}),
    "시설수의": ("/peb/openNego/openNegoPlanView.do", {"pageDivs": "E", "csrt_numb": "2026-14402", "negn_pldt": _d(0),
             "dprt_code": "MCN", "ordr_year": "2026", "negn_degr": "1", "anmt_numb": "MCN0044"}),
}
END_DAYS = {"국내경쟁": 3, "국외경쟁": 10, "시설경쟁": 3, "국내수의": 2, "시설수의": 2}
BID_NO = {
    "국내경쟁": "D2B-국내경쟁-2026ERA00055606N-3",
    "국외경쟁": "D2B-국외경쟁-2026ELA0026BBAL6013005-1",
    "시설경쟁": "D2B-시설경쟁-2026LGP01272026-15117-1",
    "국내수의": "D2B-국내수의-2026HCF019135442-2",
    "시설수의": "D2B-시설수의-MCN00442026-14402-1",
}


def _item_xml(fields: dict) -> str:
    return "<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields.items()) + "</item>"


def _body(items: list[dict]) -> bytes:
    return ("<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>" +
            "".join(_item_xml(i) for i in items) +
            f"</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>{len(items)}</totalCount></body></response>"
            ).encode()


def _mock_all(fail: str | None = None) -> dict:
    routes = {}
    for s in LISTS:
        resp = httpx.Response(500) if s.kind == fail else httpx.Response(200, content=_body([ITEMS[s.kind]]))
        routes[s.kind] = respx.get(BASE_URL + s.operation).mock(return_value=resp)
    return routes


class TestMapping:
    @pytest.mark.parametrize("kind", list(ITEMS))
    def test_bid_no_by_list(self, kind):
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS[kind]).encode()), SPEC[kind])
        assert n.bid_no == BID_NO[kind]
        assert n.title == ITEMS[kind][SPEC[kind].title]
        assert n.source == "국방전자조달"
        assert n.category == ITEMS[kind]["busiDivs"]
        assert n.budget is None
        assert n.end_date == date.today() + timedelta(days=END_DAYS[kind])  # 목록마다 다른 마감 필드
        assert n.extra == ITEMS[kind]  # 원문 전부, 이름 그대로

    @pytest.mark.parametrize("kind", list(ITEMS))
    def test_url_is_detail_page(self, kind):
        """v1.6.0 — url = 사이트 상세 화면(종전 첫 화면). 경로·파라미터 전부(메뉴 고정 파라미터 포함) 대조."""
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS[kind]).encode()), SPEC[kind])
        u = httpx.URL(n.url)
        path, params = DETAIL_PAGE[kind]
        assert (u.host, u.path) == ("www.d2b.go.kr", path)
        assert dict(u.params) == params
        assert n.detail_url == ""

    @pytest.mark.parametrize("kind,busi,expected", [
        ("시설경쟁", "공사", "https://www.d2b.go.kr/peb/bid/announceList.do?key=41"),
        ("국내경쟁", "물품", "https://www.d2b.go.kr/pdb/bid/goodsBidAnnounceList.do?key=13"),
        ("국내경쟁", "용역", "https://www.d2b.go.kr/psb/bid/serviceBidAnnounceList.do?key=32"),
    ])
    def test_nominated_competition_goes_to_list_page(self, kind, busi, expected):
        """지명경쟁은 상세가 로그인 필요(실측 6/6) → 구분별 목록 화면."""
        fields = {**ITEMS[kind], "cntrctMth": "지명경쟁", "busiDivs": busi}
        assert _item_to_notice(etree.fromstring(_item_xml(fields).encode()), SPEC[kind]).url == expected

    @pytest.mark.parametrize("kind,field,value", [
        ("국내경쟁", "pblancSe", "취소공고"), ("시설경쟁", "pblancSe", "취소공고"),
        ("국내수의", "progrsSttus", "공개협상취소"), ("시설수의", "progrsSttus", "공개협상취소"),
    ])
    def test_source_cancel_mark_is_cancelled(self, kind, field, value):
        """v1.6.0 — 출처가 명시한 취소(bidwatch F-017과 같은 키·값) → cancelled. 정상 값이면 마감일 판정."""
        n = _item_to_notice(etree.fromstring(_item_xml({**ITEMS[kind], field: value}).encode()), SPEC[kind])
        assert n.status == "cancelled"
        normal = _item_to_notice(etree.fromstring(_item_xml({**ITEMS[kind], field: "정상"}).encode()), SPEC[kind])
        assert normal.status == "ongoing"

    def test_domestic_fields(self):
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS["국내경쟁"]).encode()), SPEC["국내경쟁"])
        assert n.organization == "우주지휘통신총괄계약팀"
        assert n.start_date == date.today()
        assert n.end_date == date.today() + timedelta(days=3)

    def test_foreign_list_has_no_start_and_no_org(self):
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS["국외경쟁"]).encode()), SPEC["국외경쟁"])
        assert n.start_date is None  # 목록에 공고일 필드가 없다
        assert n.organization == ""  # 발주기관 필드도 없다 — v1.6.0 원칙 ②(종전 "방위사업청" 상수)

    @pytest.mark.parametrize("kind", ["국내수의", "시설수의"])
    def test_negotiation_has_no_start_date(self, kind):
        """ntatPlanDate는 앞으로의 협상 예정일이다(실측 최대 2027-05-06) — 공고일로 쓰지 않는다."""
        fields = {**ITEMS[kind], "ntatPlanDate": _d(200)}
        n = _item_to_notice(etree.fromstring(_item_xml(fields).encode()), SPEC[kind])
        assert n.start_date is None
        assert n.end_date == date.today() + timedelta(days=2)
        assert n.extra["ntatPlanDate"] == _d(200)

    def test_cancel_notice_is_separate_from_original(self):
        """회귀(2026-09-26 실측): 취소공고는 pblancOdr만 오르고 g2bPblancOdr는 그대로다 — 원공고와 다른 bid_no여야 한다."""
        original = {**ITEMS["국내경쟁"], "pblancOdr": "1", "g2bPblancOdr": "01", "pblancSe": "정상공고"}
        cancel = {**ITEMS["국내경쟁"], "pblancOdr": "2", "g2bPblancOdr": "01", "pblancSe": "취소공고"}
        a, b = (_item_to_notice(etree.fromstring(_item_xml(f).encode()), SPEC["국내경쟁"]) for f in (original, cancel))
        assert a.bid_no != b.bid_no


class TestFetch:
    @respx.mock
    async def test_collects_all_five_lists(self):
        _mock_all()
        result = await D2bCollector(api_key="k").collect(days=7)
        assert sorted(n.bid_no for n in result.notices) == sorted(BID_NO.values())
        assert result.errors == []
        assert result.pages_processed == 5

    @respx.mock
    async def test_one_list_failure_keeps_others(self):
        _mock_all(fail="국외경쟁")
        result = await D2bCollector(api_key="SECRETKEY123").collect(days=7)
        assert len(result.notices) == 4
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "[국방전자조달 국외경쟁] 페이지 1 요청 실패" in result.errors[0]
        assert "SECRETKEY123" not in result.errors[0]

    @respx.mock
    async def test_list_date_params(self):
        routes = _mock_all()
        await D2bCollector(api_key="k").collect(days=7)
        p = {k: r.calls[0].request.url.params for k, r in routes.items()}
        for kind in ("국내경쟁", "시설경쟁"):
            assert (p[kind]["anmtDateBegin"], p[kind]["anmtDateEnd"]) == (_d(-7), _d(0))
            assert "opengDateBegin" not in p[kind]
        # 국외: 개찰일이 필수 — 개찰은 공고 뒤라 시작 = 공고일 범위 시작(1년 전으로 넓히면 API가 0건), 끝 2년 뒤(v1.6.0, 종전 1년)
        assert (p["국외경쟁"]["anmtDateBegin"], p["국외경쟁"]["opengDateBegin"]) == (_d(-7), _d(-7))
        assert p["국외경쟁"]["opengDateEnd"] == _d(730)
        # 수의 2종: 공고일 필터가 없다 — 견적서 마감 (오늘-days)~1년 뒤(v1.6.0 — 종전 오늘부터는 기간 안에 마감된 공고를 걸렀다)
        for kind in ("국내수의", "시설수의"):
            assert (p[kind]["prqudoPresentnClosDateBegin"], p[kind]["prqudoPresentnClosDateEnd"]) == (_d(-7), _d(365))
            assert "anmtDateBegin" not in p[kind]

    @respx.mock
    async def test_unbuildable_url_is_reported(self):
        """주소 필드가 비면 첫 화면 url로 공고는 돌려주고, 그 사실과 구분별 건수를 errors에(조용히 넘기지 않는다)."""
        items = {k: dict(v) for k, v in ITEMS.items()}
        del items["국내경쟁"]["orntCode"]
        del items["시설수의"]["ntatPlanDate"]
        for s in LISTS:
            respx.get(BASE_URL + s.operation).mock(return_value=httpx.Response(200, content=_body([items[s.kind]])))
        result = await D2bCollector(api_key="k").collect(days=7)
        assert len(result.notices) == 5
        assert sorted(n.bid_no for n in result.notices if n.url == "https://www.d2b.go.kr/") == [
            BID_NO["국내경쟁"], BID_NO["시설수의"]]
        assert len(result.errors) == 1
        assert "국내경쟁 1건" in result.errors[0] and "시설수의 1건" in result.errors[0]


# ── fetch_detail (v1.5.0) ── 목록 조회 행: 상세에 필요한 필드(orntCode·pblancYear·pblancSeCode·iemNo·ntatPlanDate)까지 든 실측 모양
LOOKUP_ROWS = {
    # 같은 공고의 취소 차수가 함께 온다(실측: 원공고 B 차수 1·취소 J 차수 2) — 다른 차수 행을 앞에 둬 차수 비교를 잰다
    "시설경쟁": [ITEMS["시설경쟁"] | {"pblancYear": "2026", "orntCode": "LGP", "pblancNo": "LGP0127", "pblancSeCode": "J",
                               "pblancOdr": "2"},
             ITEMS["시설경쟁"] | {"pblancYear": "2026", "orntCode": "LGP", "pblancNo": "LGP0127", "pblancSeCode": "A"}],
    "국내수의": [ITEMS["국내수의"] | {"orntCode": "HCF", "dcsNo": "99999"},  # 같은 공고번호의 다른 판단번호(연도 재사용)
             ITEMS["국내수의"] | {"orntCode": "HCF", "ntatPlanDate": "20260930"}],
    "시설수의": [ITEMS["시설수의"] | {"orntCode": "MCN", "cntrwkNo": "2025-14402"},  # 전년도 공사번호에 같은 공고번호
             ITEMS["시설수의"] | {"orntCode": "MCN", "ntatPlanDate": "20261001"}],
}
DETAIL_ITEM = {"cntrwkNm": "00부대 사무실 환경 개선공사", "estmPrce": "439988182", "areaLmttList": "[16] 경기도",
               "lcnsLmttList": "[0001] 토목공사업^[0003] 토목건축공사업", "baseCoam": "0", "chargerNm": "정재헌"}


def _detail_xml(items: list[dict]) -> bytes:
    """상세 응답 — body 아래 item이 바로 온다(items·totalCount 없음). 0건이면 <body/>(실측)."""
    inner = "".join(_item_xml(i) for i in items)
    return ("<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>" +
            (f"<body>{inner}</body>" if items else "<body/>") + "</response>").encode()


def _detail_route(kind: str, items: list[dict] | None = None):
    op = DETAILS[kind].operation
    return respx.get(BASE_URL + op).mock(
        return_value=httpx.Response(200, content=_detail_xml([DETAIL_ITEM] if items is None else items)))


class TestFetchDetail:
    @pytest.mark.parametrize("kind, expected", [
        ("국내경쟁", {"demandYear": "2026", "orntCode": "ERA", "pblancNo": "ERA0005", "dcsNo": "5606N", "pblancOdr": "3"}),
        ("국외경쟁", {"pblancYear": "2026", "pblancNo": "ELA0026", "dcsNo": "BBAL6013", "groupNo": "005", "pblancOdr": "1"}),
    ])
    @respx.mock
    async def test_detail_competitive_params(self, kind, expected):
        route = _detail_route(kind)
        d = await D2bCollector(api_key="k").fetch_detail(BID_NO[kind])
        assert route.call_count == 1  # 목록 조회 없이 1회
        params = dict(route.calls[0].request.url.params)
        params.pop("serviceKey")
        assert params == expected
        assert d["estmPrce"] == "439988182"

    @pytest.mark.parametrize("kind, expected", [
        ("시설경쟁", {"pblancYear": "2026", "orntCode": "LGP", "pblancNo": "LGP0127", "cntrwkNo": "2026-15117",
                  "pblancSeCode": "A", "pblancOdr": "1"}),
        ("국내수의", {"demandYear": "2026", "orntCode": "HCF", "pblancNo": "HCF0191", "dcsNo": "35442", "iemNo": "***",
                  "pblancOdr": "2", "ntatPlanDate": "20260930"}),
        ("시설수의", {"orntCode": "MCN", "pblancNo": "MCN0044", "cntrwkNo": "2026-14402", "pblancOdr": "1",
                  "ntatPlanDate": "20261001"}),
    ])
    @respx.mock
    async def test_detail_lookup_then_detail(self, kind, expected):
        """상세 필수 값이 bid_no에 없다 — 목록을 키로 1회 조회해 같은 키·차수 행에서 얻는다(다른 판단번호·공사번호·차수 행은 건너뜀)."""
        lookup = respx.get(BASE_URL + SPEC[kind].operation).mock(
            return_value=httpx.Response(200, content=_body(LOOKUP_ROWS[kind])))
        route = _detail_route(kind)
        await D2bCollector(api_key="k").fetch_detail(BID_NO[kind])
        assert lookup.call_count == 1 and route.call_count == 1
        params = dict(route.calls[0].request.url.params)
        params.pop("serviceKey")
        assert params == expected
        q = lookup.calls[0].request.url.params
        if kind == "시설경쟁":
            assert q["g2bPblancNo"] == "2026LGP01272026-15117" and "prqudoPresentnClosDateBegin" not in q
        else:  # 수의 목록은 견적서 마감 범위를 빼면 0건 — 앞뒤로 넓게
            assert q[DETAILS[kind].lookup] == expected["pblancNo"]
            assert q["prqudoPresentnClosDateBegin"] < _d(-365) and q["prqudoPresentnClosDateEnd"] > _d(365)

    @respx.mock
    async def test_detail_maps_item(self):
        _detail_route("국내경쟁")
        d = await D2bCollector(api_key="k").fetch_detail(BID_NO["국내경쟁"])
        assert d == DETAIL_ITEM | {"attachments": [], "content": ""}  # 원문 전부·이름 그대로, ^ 구분 문자열 원문, 0 포함

    @respx.mock
    async def test_detail_not_found_raises(self):
        """없는 공고와 파라미터 불일치가 같은 빈 응답(resultCode 00 + <body/>)이다 — 빈 dict로 넘기지 않는다."""
        _detail_route("국내경쟁", items=[])
        with pytest.raises(ValueError, match="결과 없음"):
            await D2bCollector(api_key="k").fetch_detail(BID_NO["국내경쟁"])

    @respx.mock
    async def test_detail_lookup_miss_raises_without_detail_call(self):
        respx.get(BASE_URL + SPEC["시설수의"].operation).mock(
            return_value=httpx.Response(200, content=_body([LOOKUP_ROWS["시설수의"][0]])))  # 다른 공사번호 행만
        route = _detail_route("시설수의")
        with pytest.raises(ValueError, match="같은 키·차수 행을 못 찾음"):
            await D2bCollector(api_key="k").fetch_detail(BID_NO["시설수의"])
        assert route.call_count == 0

    @respx.mock
    async def test_detail_error_code_raises(self):
        respx.get(BASE_URL + DETAILS["국외경쟁"].operation).mock(return_value=httpx.Response(200, content=(
            b"<response><header><resultCode>22</resultCode><resultMsg>LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS</resultMsg>"
            b"</header></response>")))
        with pytest.raises(ValueError, match="API 에러: 22"):
            await D2bCollector(api_key="k").fetch_detail(BID_NO["국외경쟁"])

    @respx.mock
    async def test_http_error_raises_and_key_masked(self):
        respx.get(BASE_URL + DETAILS["국내경쟁"].operation).mock(return_value=httpx.Response(500))
        with pytest.raises(RuntimeError) as exc:
            await D2bCollector(api_key="SECRETKEY123").fetch_detail(BID_NO["국내경쟁"])
        assert "SECRETKEY123" not in str(exc.value)
        assert exc.value.__cause__ is None and exc.value.__suppress_context__  # 원 예외(키 든 URL)를 체인에 남기지 않는다

    @pytest.mark.parametrize("bad", ["2026ERA00055606N", "D2B-국내경쟁-2026ERA00055606N", "D2B-국내경쟁-2026ERA00055606N-X",
                                     "D2B-없는구분-X-1",
                                     "D2B-국외경쟁-2026ELA0026-1", "ALIO-1"])
    @respx.mock
    async def test_bad_bid_no_raises_without_request(self, bad):
        route = respx.get(url__startswith=BASE_URL)
        with pytest.raises(ValueError, match="bid_no"):
            await D2bCollector(api_key="k").fetch_detail(bad)
        assert route.call_count == 0
