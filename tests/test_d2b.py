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


# 목록별 실측 원문 모양(2026-09-25 d2b_raw) — 값만 실행 시점 상대 날짜로
ITEMS = {
    "국내경쟁": {"bidNm": "TICN용 2차전지 모듈", "g2bPblancNo": "2026ERA00055606N", "g2bPblancOdr": "03",
              "pblancNo": "ERA0005", "pblancOdr": "3", "dcsNo": "5606N", "demandYear": "2026", "ornt": "우주지휘통신총괄계약팀",
              "busiDivs": "물품", "pblancDate": _d(0), "biddocPresentnClosDt": _d(3) + "1000", "bsicExpt": "17757000"},
    "국외경쟁": {"bsnsNm": "K5방독면 기밀시험장비", "g2bPblancNo": "2026ELA0026BBAL6013005", "g2bPblancOdr": "01",
              "pblancNo": "ELA0026", "pblancOdr": "1", "dcsNo": "BBAL6013", "groupNo": "005", "busiDivs": "용역",
              "bidRegistClosDt": _d(10) + "1400", "pblancCanclAt": "N"},
    "시설경쟁": {"cntrwkNm": "00부대 사무실 환경 개선공사", "g2bPblancNo": "2026LGP01272026-15117", "g2bPblancOdr": "01",
              "pblancOdr": "1", "cntrwkNo": "2026-15117", "ornt": "제1군수지원사령부", "busiDivs": "공사", "pblancDate": _d(0),
              "biddocPresentnClosDt": _d(3) + "1000", "baseAmnt": "268161450"},
    "국내수의": {"othbcNtatNm": "시험세트 서보용 외주정비", "demandYear": "2026", "pblancNo": "HCF0191", "dcsNo": "35442",
              "pblancOdr": "2", "ornt": "공군군수사령부", "busiDivs": "용역", "ntatPlanDate": _d(0),
              "prqudoPresentnClosDt": _d(2) + "1000", "budgetAmount": "43120000", "iemNo": "***"},
    "시설수의": {"cntrwkNm": "조사본부 야외데크 보수공사", "pblancNo": "MCN0044", "cntrwkNo": "2026-14402", "pblancOdr": "1",
              "ornt": "국방부근무지원단", "busiDivs": "공사", "ntatPlanDate": _d(0), "prqudoPresentnClosDt": _d(2) + "1000"},
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
        assert n.url == "https://www.d2b.go.kr/"  # 상세 링크 필드가 없다 — 첫 화면
        assert n.extra == ITEMS[kind]  # 원문 전부, 이름 그대로

    def test_domestic_fields(self):
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS["국내경쟁"]).encode()), SPEC["국내경쟁"])
        assert n.organization == "우주지휘통신총괄계약팀"
        assert n.start_date == date.today()
        assert n.end_date == date.today() + timedelta(days=3)

    def test_foreign_list_has_no_start_and_fixed_org(self):
        n = _item_to_notice(etree.fromstring(_item_xml(ITEMS["국외경쟁"]).encode()), SPEC["국외경쟁"])
        assert n.start_date is None  # 목록에 공고일 필드가 없다
        assert n.organization == "방위사업청"

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
        # 국외: 개찰일이 필수 — 공고일 범위와 함께 개찰일 기준일~1년 뒤
        assert (p["국외경쟁"]["anmtDateBegin"], p["국외경쟁"]["opengDateBegin"]) == (_d(-7), _d(-7))
        assert p["국외경쟁"]["opengDateEnd"] == _d(365)
        # 수의 2종: 공고일 필터가 없다 — 견적서 마감 오늘~1년 뒤(진행 중 전량)
        for kind in ("국내수의", "시설수의"):
            assert (p[kind]["prqudoPresentnClosDateBegin"], p[kind]["prqudoPresentnClosDateEnd"]) == (_d(0), _d(365))
            assert "anmtDateBegin" not in p[kind]


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
