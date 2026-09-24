"""알리오 수집기(alio.py) 단위 테스트 + 실호출 1건(integration)."""

import os
from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx

from bid_collectors import AlioCollector
from bid_collectors.alio import API_URL, _item_to_notice, _parse_response


def _d(days_ago: int) -> str:
    """실행 시점 기준 상대 날짜를 알리오 형식(YYYY.MM.DD)으로 — 고정 날짜는 cutoff에 걸려 시간이 지나면 깨진다."""
    return (date.today() - timedelta(days=days_ago)).strftime("%Y.%m.%d")


def _item(seq: int, days_ago: int = 0, title: str = "정보시스템 유지관리 용역", org: str = "한국수자원공사") -> dict:
    return {"rtitle": title, "pname": org, "bdate": _d(days_ago), "bidInfoEndDt": _d(days_ago - 7),
            "cdNo": "B1030", "seq": seq, "rnum": 1}


def _body(items: list[dict], total: int = 100, status: str = "success") -> dict:
    return {"status": status, "message": None, "data": {"result": items, "totalCnt": total, "todayCnt": 0}}


class TestMapping:
    def test_item_maps_to_notice(self):
        n = _item_to_notice(_item(3580351, days_ago=0, title="자연적응훈련장  모노레일 제작", org="국립공원공단"))
        assert n.source == "알리오"
        assert n.bid_no == "ALIO-3580351"
        assert n.title == "자연적응훈련장 모노레일 제작"  # 연속 공백 정리
        assert n.organization == "국립공원공단"
        assert n.start_date == date.today()
        assert n.end_date == date.today() + timedelta(days=7)
        assert n.status == "ongoing"
        assert n.url == "https://alio.go.kr/occasional/bidDtl.do?seq=3580351"
        assert n.detail_url == n.url

    def test_non_success_status_raises(self):
        with pytest.raises(ValueError, match="status='fail'"):
            _parse_response(_body([], status="fail"))


class TestInit:
    def test_no_api_key_needed(self):
        with patch.dict(os.environ, {}, clear=True):
            assert AlioCollector().api_key is None


class TestFetch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_stops_at_cutoff_without_next_page(self):
        """공고일 최신순 — 기준일보다 오래된 항목이 나오면 그 뒤는 버리고 다음 페이지를 부르지 않는다."""
        route = respx.get(API_URL).mock(return_value=httpx.Response(200, json=_body(
            [_item(10, 0), _item(9, 1), _item(8, 5), _item(7, 6)])))
        result = await AlioCollector().collect(days=2)
        assert [n.bid_no for n in result.notices] == ["ALIO-10", "ALIO-9"]
        assert route.call_count == 1
        assert result.is_partial is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_continues_to_next_page_until_cutoff(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(20, 0), _item(19, 0)])),
            httpx.Response(200, json=_body([_item(18, 1), _item(17, 9)])),
        ])
        result = await AlioCollector().collect(days=3)
        assert [n.bid_no for n in result.notices] == ["ALIO-20", "ALIO-19", "ALIO-18"]
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_first_page_failure_is_reported_not_empty_success(self):
        respx.get(API_URL).mock(return_value=httpx.Response(500))
        result = await AlioCollector().collect(days=1)
        assert result.notices == []
        assert result.is_partial is True
        assert "페이지 1 요청 실패" in result.errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_second_page_failure_keeps_first_page(self):
        respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(30, 0)])),
            httpx.Response(200, json=_body([], status="fail")),
        ])
        result = await AlioCollector().collect(days=3)
        assert [n.bid_no for n in result.notices] == ["ALIO-30"]
        assert result.is_partial is True
        assert "페이지 2 요청 실패" in result.errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_max_pages_truncation_reported_with_total(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(100 + i, 0)], total=83801)) for i in range(3)
        ])
        result = await AlioCollector().collect(days=30, max_pages=2)
        assert route.call_count == 2
        assert len(result.notices) == 2
        assert result.is_partial is True
        assert "max_pages=2" in result.errors[0] and "83801건" in result.errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_item_without_seq_is_skipped_and_reported(self):
        broken = _item(0, 0)
        del broken["seq"]
        respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(40, 0), broken])),
            httpx.Response(200, json=_body([])),
        ])
        result = await AlioCollector().collect(days=1)
        assert [n.bid_no for n in result.notices] == ["ALIO-40"]
        assert result.is_partial is True
        assert "1건 건너뜀" in result.errors[0]


class TestRequiredFields:
    """비공식 JSON이라 필드 이름이 바뀌면 조용히 빈 공고가 저장되지 않고 errors로 드러나야 한다."""

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("field,value", [
        ("rtitle", None), ("rtitle", "   "), ("pname", None), ("pname", ""),
        ("bdate", None), ("bdate", "날짜아님"),
    ])
    async def test_missing_required_field_is_skipped_with_reason(self, field, value):
        broken = _item(99, 0)  # seq는 0이 아닌 값으로 — 0을 쓰면 "seq 없음"으로 먼저 걸려 검사 대상 필드를 못 본다
        if value is None:
            del broken[field]
        else:
            broken[field] = value
        respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(50, 0), broken])),
            httpx.Response(200, json=_body([])),
        ])
        result = await AlioCollector().collect(days=1)
        assert [n.bid_no for n in result.notices] == ["ALIO-50"]
        assert result.is_partial is True
        assert "1건 건너뜀" in result.errors[0] and field in result.errors[0]

    def test_seq_zero_is_a_valid_id(self):
        """숫자 0을 '없음'으로 오판하지 않는다."""
        assert _item_to_notice(_item(0, 0)).bid_no == "ALIO-0"

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_deadline_is_kept(self):
        """마감일은 원래 비어 오는 공고가 있다(474건 중 10건) — 버리면 안 된다."""
        no_end = _item(60, 0)
        del no_end["bidInfoEndDt"]
        respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([no_end])),
            httpx.Response(200, json=_body([])),
        ])
        result = await AlioCollector().collect(days=1)
        assert [n.bid_no for n in result.notices] == ["ALIO-60"]
        assert result.notices[0].end_date is None
        assert result.is_partial is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_api_returns_recent_notices():
    """실호출 — 최근 3일 공고가 1건 이상, 필드가 채워져 온다."""
    result = await AlioCollector().collect(days=3, max_pages=5)
    assert result.notices, result.errors
    n = result.notices[0]
    assert n.bid_no.startswith("ALIO-") and n.title and n.organization and n.start_date
