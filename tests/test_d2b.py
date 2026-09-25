"""국방전자조달(d2b) 수집기(d2b.py) 단위 테스트 — F-013."""

from datetime import date, timedelta

import httpx
import pytest
import respx
from lxml import etree

from bid_collectors import D2bCollector
from bid_collectors.d2b import BASE_URL, LISTS, _item_to_notice

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
        assert n.end_date is not None
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
