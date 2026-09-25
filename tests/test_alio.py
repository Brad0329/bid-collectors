"""알리오 수집기(alio.py) 단위 테스트 + 실호출 1건(integration)."""

import os
from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx

from bid_collectors import AlioCollector
from bid_collectors.alio import API_URL, DETAIL_API_URL, _item_to_notice, _parse_response


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
    async def test_interleaved_old_item_does_not_stop_collection(self):
        """회귀(2026-09-24 실측): 목록은 등록 순이라 오래된 공고일 항목이 새 항목 사이에 낀다(강원랜드 9/21이 9/22 사이).
        종전 코드는 그 항목에서 멈춰 뒤의 새 공고 472건을 errors 없이 놓쳤다. 오래된 항목만 버리고 계속 받아야 한다."""
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(30, 0), _item(29, 5), _item(28, 0)])),  # 사이에 오래된 것
            httpx.Response(200, json=_body([_item(27, 1)])),
            httpx.Response(200, json=_body([_item(26, 5)])),
            httpx.Response(200, json=_body([_item(25, 6)])),
        ])
        result = await AlioCollector().collect(days=2)
        assert [n.bid_no for n in result.notices] == ["ALIO-30", "ALIO-28", "ALIO-27"]
        assert route.call_count == 4
        assert result.is_partial is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_stops_after_two_consecutive_old_pages(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(20, 0)])),
            httpx.Response(200, json=_body([_item(19, 5)])),
            httpx.Response(200, json=_body([_item(18, 6)])),
            httpx.Response(200, json=_body([_item(17, 0)])),  # 여기까지 오면 안 된다
        ])
        result = await AlioCollector().collect(days=2)
        assert [n.bid_no for n in result.notices] == ["ALIO-20"]
        assert route.call_count == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_old_page_does_not_stop(self):
        """과거 공고일 공고를 한꺼번에 등록하면 한 페이지가 통째로 오래될 수 있다 — 한 페이지로는 멈추지 않는다."""
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(40, 0)])),
            httpx.Response(200, json=_body([_item(39, 5), _item(38, 6)])),
            httpx.Response(200, json=_body([_item(37, 0)])),
            httpx.Response(200, json=_body([_item(36, 5)])),
            httpx.Response(200, json=_body([_item(35, 6)])),
        ])
        result = await AlioCollector().collect(days=2)
        assert [n.bid_no for n in result.notices] == ["ALIO-40", "ALIO-37"]
        assert route.call_count == 5

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


def _file(n: int) -> dict:
    return {"fileNm": f"공고문{n}.hwp", "fileNo": f"https://www.g2b.go.kr/down?fileSeq={n}"}


def _dtl(**over) -> dict:
    """findBidDtl.json의 data.bidDtl — seq 3580350 실응답(2026-09-25)에서 줄인 것. 빈 값·0이 섞여 있다."""
    d = {"rnum": 1, "disclosureNo": "2026092303245364", "boardNo": 3580350, "gbn": "2", "critQuar": None,
         "apbaId": "C0213", "pname": "한국생산기술연구원", "seq": "3580350", "rtitle": "개기공분포측정기",
         "refrUrl": "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK01743684&bidPbancOrd=000",
         "content": "", "author": None, "ingStatus": "1", "totContAmt": 0, "bidType": "1",
         "bFiles": "https://www.g2b.go.kr/down?fileSeq=1|공고문1.hwp", "logo": " "}
    d.update(over)
    return d


def _dtl_body(dtl: dict | None = None, files=None, status: str = "success") -> dict:
    return {"status": status, "message": None, "data": {"bidDtl": dtl if dtl is not None else _dtl(), "fileList": files}}


class TestFetchDetail:
    @pytest.mark.asyncio
    @respx.mock
    async def test_maps_detail(self):
        route = respx.get(DETAIL_API_URL).mock(
            return_value=httpx.Response(200, json=_dtl_body(files=[_file(1), _file(2), _file(3)])))
        d = await AlioCollector().fetch_detail("ALIO-3580350")
        assert route.calls[0].request.url.params["seq"] == "3580350"
        assert d["attachments"] == [{"name": f"공고문{n}.hwp", "url": f"https://www.g2b.go.kr/down?fileSeq={n}"}
                                    for n in (1, 2, 3)]
        nonempty = {k for k, v in _dtl().items() if v is not None and str(v).strip() != ""}
        assert set(d) == nonempty | {"attachments", "content"}  # 원래 이름 그대로, 골라 빼지 않음
        assert d["totContAmt"] == 0  # 0은 값이다
        assert d["refrUrl"].startswith("https://www.g2b.go.kr/")
        assert d["content"] == ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_content_html_stripped(self):
        respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(
            200, json=_dtl_body(_dtl(content="<p>사업 <b>개요</b></p>"), files=[])))
        d = await AlioCollector().fetch_detail("ALIO-1")
        assert d["content"] == "사업 개요"

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("files", [None, []])
    async def test_no_files_gives_empty_list(self, files):
        respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(200, json=_dtl_body(files=files)))
        d = await AlioCollector().fetch_detail("ALIO-1")
        assert d["attachments"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_status_raises(self):
        # 실측(2026-09-25): 없는 seq·abc·빈 값·0 전부 HTTP 200 + 이 모양
        respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(
            200, json={"status": "error", "message": "시스템 에러입니다. 관리자에게 문의하세요.", "data": None}))
        with pytest.raises(ValueError, match="status='error' message='시스템 에러입니다. 관리자에게 문의하세요.'"):
            await AlioCollector().fetch_detail("ALIO-999999999")

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_raises(self):
        respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await AlioCollector().fetch_detail("ALIO-1")

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("body, match", [
        ({"status": "success", "data": {"fileList": []}}, "bidDtl 없음"),
        ({"status": "success", "data": None}, "bidDtl 없음"),
        (_dtl_body(files="a.hwp"), "fileList가 list가 아님"),
    ])
    async def test_malformed_raises(self, body, match):
        respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(ValueError, match=match):
            await AlioCollector().fetch_detail("ALIO-1")

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("bid_no", ["3580350", "KSTARTUP-1", "ALIO-", "ALIO- "])
    async def test_bad_bid_no_raises_without_request(self, bid_no):
        route = respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(200, json=_dtl_body(files=[])))
        with pytest.raises(ValueError, match="형식이 아님"):
            await AlioCollector().fetch_detail(bid_no)
        assert route.call_count == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_collect_does_not_call_detail_api(self):
        detail = respx.get(DETAIL_API_URL).mock(return_value=httpx.Response(200, json=_dtl_body(files=[])))
        respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_body([_item(10), _item(9)])),
            httpx.Response(200, json=_body([])),
        ])
        result = await AlioCollector().collect(days=1)
        assert len(result.notices) == 2
        assert detail.call_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_fetch_detail():
    """(v1.3.0) 실호출 — 목록 1페이지(10건)의 seq마다 상세 조회: 키 집합 == 비어 있지 않은 bidDtl 키 ∪ 2개, 첨부 건수 == fileList 건수.
    독립 계산을 위해 같은 seq의 원응답을 한 번 더 받는다."""
    from bid_collectors.utils.http import create_client

    collector = AlioCollector()
    async with create_client(timeout=20.0) as client:
        resp = await client.get(API_URL, params={"type": "title", "word": "", "pageNo": "1", "area": ""})
        items, _ = _parse_response(resp.json())
        assert items, "알리오 1페이지가 비어 있음"
        n_files = n_refr = 0
        for item in items:
            d = await collector.fetch_detail(f"ALIO-{item['seq']}")
            raw = (await client.get(DETAIL_API_URL, params={"seq": item["seq"]})).json()["data"]
            expected = {k for k, v in raw["bidDtl"].items() if v is not None and str(v).strip() != ""}
            assert set(d) == expected | {"attachments", "content"}, f"seq={item['seq']}"
            assert len(d["attachments"]) == len(raw.get("fileList") or []), f"seq={item['seq']}"
            n_files += len(d["attachments"])
            n_refr += "refrUrl" in d
    print(f"\n[fetch_detail 실측] 알리오 1페이지 {len(items)}건: 첨부 합계 {n_files}개, refrUrl {n_refr}건")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_fetch_detail_unknown_seq_raises():
    """실호출 고장 시나리오 — 없는 seq는 예외(None·빈 dict로 조용히 돌아오지 않는다)."""
    with pytest.raises(ValueError, match="status='error'"):
        await AlioCollector().fetch_detail("ALIO-999999999")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_api_returns_recent_notices():
    """실호출 — 최근 3일 공고가 1건 이상, 필드가 채워져 온다."""
    result = await AlioCollector().collect(days=3, max_pages=5)
    assert result.notices, result.errors
    # max_pages=5는 3일치에 못 닿아 절단 보고는 정상 — 그 외(항목 건너뜀 등 형식 변경 신호)는 실패로 본다
    real = [e for e in result.errors if not e.startswith("max_pages=")]
    assert real == [], f"수집 에러 발생: {real}"
    n = result.notices[0]
    assert n.bid_no.startswith("ALIO-") and n.title and n.organization and n.start_date


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_api_extra_matches_raw_items():
    """(v1.2.5 원칙 ①) 실응답 1페이지(10건)의 항목마다 extra 키 집합 == 비어 있지 않은 키 집합 — 헬퍼를 쓰지 않고 독립 계산으로 잰다."""
    from bid_collectors.utils.http import create_client

    async with create_client(timeout=20.0) as client:
        resp = await client.get(API_URL, params={"type": "title", "word": "", "pageNo": "1", "area": ""})
        resp.raise_for_status()
        items, _ = _parse_response(resp.json())
    assert items, "알리오 1페이지가 비어 있음"
    for item in items:
        expected = {k for k, v in item.items() if v is not None and str(v).strip() != ""}
        assert set(_item_to_notice(item).extra) == expected, f"seq={item.get('seq')}"
    print(f"\n[extra 실측] 알리오 1페이지 {len(items)}건 전부 extra == 비어 있지 않은 키")
