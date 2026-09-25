"""한국수자원공사 입찰공고 수집기(kwater.py) 단위 테스트 — F-014."""

from datetime import date, timedelta

import httpx
import pytest
import respx

from bid_collectors import KwaterCollector
from bid_collectors.kwater import BASE_URL, OPERATIONS, _item_to_notice, _months, parse_json

TODAY = date.today()


def _ymd(days: int) -> int:
    return int((TODAY + timedelta(days=days)).strftime("%Y%m%d"))  # 공고일은 정수로 온다(실측)


def _item(no: str = "B5202603396", days_ago: int = 0, end: object = None) -> dict:
    return {"cntrctDeptNm": "재무관리처", "cntrctDivNm": "용역", "ctrmthdCdNm": "일반경쟁", "intnChargerNm": "담당",
            "tndrPartcptEntrpsCo": 0, "tndrPbanno": no, "tndrPblancDe": _ymd(-days_ago),
            "tndrPblancEnddt": _ymd(7) if end is None else end, "tndrPblancNm": "한강유역 입상활성탄 흡착성능 회복 용역",
            "tndrPlnprc": 0, "tndrPrqudoCo": 0, "tndrStat": "참가신청"}


def _body(items, total: int | None = None) -> dict:
    """items: list → 모양을 실측대로(0건 "", 1건 dict, 여러 건 list)."""
    total = (len(items) if isinstance(items, list) else 1) if total is None else total
    if isinstance(items, list):
        shaped = "" if not items else {"item": items[0] if len(items) == 1 else items}
    else:
        shaped = items
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                         "body": {"items": shaped, "numOfRows": 50, "pageNo": 1, "totalCount": total}}}


def _mock(by_op: dict[str, list] | None = None):
    """오퍼레이션별 항목(없으면 0건). 모든 달에 같은 응답."""
    routes = {}
    for op in OPERATIONS:
        items = (by_op or {}).get(op, [])
        routes[op] = respx.get(BASE_URL + op).mock(return_value=httpx.Response(200, json=_body(items)))
    return routes


class TestMapping:
    def test_item_maps_to_notice(self):
        n = _item_to_notice(_item())
        assert n.source == "수자원공사"
        assert n.bid_no == "KWATER-B5202603396"
        assert n.organization == "한국수자원공사"
        assert n.start_date == TODAY
        assert n.end_date == TODAY + timedelta(days=7)
        assert n.category == "용역"
        assert n.budget is None
        assert n.url == "https://ebid.kwater.or.kr/fz?bidno=B5202603396"
        assert n.extra["tndrPlnprc"] == 0  # 0도 값이다 — 원문 그대로
        assert n.extra["tndrPblancDe"] == _ymd(0)

    def test_dash_deadline_is_none(self):
        n = _item_to_notice(_item(end="-"))
        assert n.end_date is None
        assert n.status == "ongoing"


class TestParse:
    @pytest.mark.parametrize("n", [0, 1, 3])
    def test_items_shapes(self, n):
        """0건 = items "", 1건 = {"item": dict}, 여러 건 = {"item": list} (실측)."""
        items, total = parse_json(httpx.Response(200, json=_body([_item(str(i)) for i in range(n)])).content)
        assert len(items) == n
        assert total == n

    def test_empty_body_is_failure(self):
        with pytest.raises(ValueError, match="빈 응답 본문"):
            parse_json(b"")

    def test_error_code_raises(self):
        body = {"response": {"header": {"resultCode": "99", "resultMsg": "ORA-01843"}, "body": {}}}
        with pytest.raises(ValueError, match="API 에러: 99 - ORA-01843"):
            parse_json(httpx.Response(200, json=body).content)

    def test_months(self):
        assert _months(date(2026, 8, 28), date(2026, 9, 26)) == ["202608", "202609"]
        assert _months(date(2025, 12, 30), date(2026, 1, 2)) == ["202512", "202601"]
        assert _months(date(2026, 9, 20), date(2026, 9, 26)) == ["202609"]


class TestFetch:
    @respx.mock
    async def test_collects_four_operations_with_params(self):
        routes = _mock({"cntrwkList": [_item("B3-1")], "servcList": [_item("B5-1"), _item("B5-2")],
                        "gdsList": [_item("B1-1")], "dmscptList": [_item("B1-9")]})
        result = await KwaterCollector(api_key="k").collect(days=1)
        assert result.errors == []
        # 모든 달에 같은 응답을 주므로 월이 2개면 같은 bid_no가 겹쳐 중복 제거된다 — 공고 수는 5건
        assert sorted(n.bid_no for n in result.notices) == sorted(
            f"KWATER-{x}" for x in ("B3-1", "B5-1", "B5-2", "B1-1", "B1-9"))
        params = routes["servcList"].calls[0].request.url.params
        assert params["searchDt"] == (TODAY - timedelta(days=1)).strftime("%Y%m")  # 기준일이 든 달부터
        assert params["_type"] == "json"
        assert params["numOfRows"] == "1000"  # 한 페이지 전량 — 나눠 받으면 정렬 불안정으로 겹침·누락
        assert routes["servcList"].call_count == len(_months(TODAY - timedelta(days=1), TODAY))  # 달마다 1회

    @respx.mock
    async def test_falls_back_to_small_pages_and_reports_overlap(self):
        """회귀(2026-09-26 실측): 전량 한 페이지가 빈 본문이면 50건씩 나눠 받는다. 서버 정렬이 불안정해 같은 행이 두 페이지에
        나오면(용역 142건 중 4건) 그만큼 다른 공고가 빠진다 — 겹친 행 수를 errors로 알린다."""
        page1 = [_item(f"N{i}") for i in range(50)]
        page2 = [_item("N49")] + [_item(f"N{i}") for i in range(50, 59)]  # N49가 두 페이지에

        def respond(request):
            p = request.url.params
            if p["numOfRows"] == "1000":
                return httpx.Response(200, content=b"")
            if p["searchDt"] != TODAY.strftime("%Y%m"):
                return httpx.Response(200, json=_body([]))
            return httpx.Response(200, json=_body(page1 if p["pageNo"] == "1" else page2, total=60))

        _mock()
        respx.get(BASE_URL + "servcList").mock(side_effect=respond)
        result = await KwaterCollector(api_key="k").collect(days=1)
        assert len(result.notices) == 59
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "[수자원공사 servcList" in result.errors[0]
        assert "60행 중 1행이 겹침" in result.errors[0]

    @respx.mock
    async def test_month_span_and_cutoff_filter(self):
        """기준일이 전월이면 전월·당월 두 달을 요청하고, 기준일 이전 공고는 거른다."""
        routes = _mock({"servcList": [_item("NEW", days_ago=1), _item("OLD", days_ago=60)]})
        result = await KwaterCollector(api_key="k").collect(days=40)
        months = [c.request.url.params["searchDt"] for c in routes["servcList"].calls]
        assert months == _months(TODAY - timedelta(days=40), TODAY)
        assert len(months) >= 2
        assert [n.bid_no for n in result.notices] == ["KWATER-NEW"]

    @respx.mock
    async def test_one_operation_failure_keeps_others(self):
        _mock({"servcList": [_item("B5-1")]})
        respx.get(BASE_URL + "gdsList").mock(return_value=httpx.Response(200, content=b""))  # 빈 본문(실측 함정)
        result = await KwaterCollector(api_key="k").collect(days=1)
        assert [n.bid_no for n in result.notices] == ["KWATER-B5-1"]
        assert result.is_partial is True
        assert all("[수자원공사 gdsList" in e and "빈 응답 본문" in e for e in result.errors)

    @respx.mock
    async def test_first_page_failure_is_reported_and_key_masked(self):
        for op in OPERATIONS:
            respx.get(BASE_URL + op).mock(return_value=httpx.Response(500))
        result = await KwaterCollector(api_key="SECRETKEY123").collect(days=1)
        assert result.notices == []
        assert len(result.errors) >= 4
        assert not any("SECRETKEY123" in e for e in result.errors)
