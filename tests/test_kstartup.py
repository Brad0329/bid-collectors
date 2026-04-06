"""K-Startup 수집기(kstartup.py) 단위 테스트."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import respx

from bid_collectors.kstartup import (
    _item_to_notice,
    KstartupCollector,
    API_URL,
    DEFAULT_PER_PAGE,
)


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

SAMPLE_ITEM = {
    "pbanc_sn": 12345,
    "biz_pbanc_nm": "테스트 창업지원사업",
    "pbanc_ctnt": "<p>창업 지원 내용</p>",
    "pbanc_rcpt_bgng_dt": "20260401",
    "pbanc_rcpt_end_dt": "20260430",
    "rcrt_prgs_yn": "Y",
    "pbanc_ntrp_nm": "창업진흥원",
    "supt_biz_clsfc": "사업화",
    "detl_pg_url": "https://www.k-startup.go.kr/detail/12345",
    "biz_aply_url": "https://apply.example.com",
    "supt_regin": "서울",
    "aply_trgt_ctnt": "예비창업자",
    "prch_cnpl_no": "02-1234-5678",
    "biz_enyy": "2026",
    "biz_trgt_age": "만 39세 이하",
}


def _make_api_response(
    items: list[dict] | None = None,
    total_count: int | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    """테스트용 JSON 응답 생성."""
    if items is None:
        items = [SAMPLE_ITEM]
    count = len(items)
    if total_count is None:
        total_count = count
    return {
        "currentCount": count,
        "data": items,
        "matchCount": total_count,
        "page": page,
        "perPage": per_page,
        "totalCount": total_count,
    }


# ---------------------------------------------------------------------------
# _item_to_notice tests
# ---------------------------------------------------------------------------


class TestItemToNotice:
    """_item_to_notice 함수 테스트."""

    def _cutoff(self) -> datetime:
        return datetime.now() - timedelta(days=30)

    def test_full_item_mapping(self):
        """전체 항목 → 올바른 Notice 필드 매핑."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice is not None
        assert notice.source == "K-Startup"
        assert notice.title == "테스트 창업지원사업"
        assert notice.organization == "창업진흥원"
        assert notice.region == "서울"
        assert notice.category == "사업화"
        assert str(notice.start_date) == "2026-04-01"
        assert str(notice.end_date) == "2026-04-30"

    def test_bid_no_format(self):
        """bid_no는 'KSTARTUP-{pbanc_sn}' 형식."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.bid_no == "KSTARTUP-12345"

    def test_url_fallback_chain_detl_pg_url(self):
        """URL 우선순위: detl_pg_url이 있으면 사용."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.url == "https://www.k-startup.go.kr/detail/12345"
        assert notice.detail_url == "https://www.k-startup.go.kr/detail/12345"

    def test_url_fallback_chain_biz_aply_url(self):
        """URL 우선순위: detl_pg_url 없으면 biz_aply_url 사용."""
        item = {**SAMPLE_ITEM, "detl_pg_url": ""}
        notice = _item_to_notice(item, self._cutoff())
        assert notice.url == "https://apply.example.com"

    def test_url_fallback_chain_biz_gdnc_url(self):
        """URL 우선순위: detl_pg_url, biz_aply_url 없으면 biz_gdnc_url 사용."""
        item = {**SAMPLE_ITEM, "detl_pg_url": "", "biz_aply_url": ""}
        item["biz_gdnc_url"] = "https://guide.example.com"
        notice = _item_to_notice(item, self._cutoff())
        assert notice.url == "https://guide.example.com"

    def test_url_fallback_all_empty(self):
        """URL: 모든 URL 필드 비어있으면 빈 문자열."""
        item = {**SAMPLE_ITEM, "detl_pg_url": "", "biz_aply_url": ""}
        notice = _item_to_notice(item, self._cutoff())
        assert notice.url == ""

    def test_content_html_cleaned(self):
        """pbanc_ctnt의 HTML이 정리됨."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert "<p>" not in notice.content
        assert "창업 지원 내용" in notice.content

    def test_content_truncated_to_500(self):
        """content는 500자로 절단."""
        long_content = "가" * 1000
        item = {**SAMPLE_ITEM, "pbanc_ctnt": long_content}
        notice = _item_to_notice(item, self._cutoff())
        assert len(notice.content) <= 500

    def test_cutoff_filtering_old_item_returns_none(self):
        """cutoff 이전 항목 → None."""
        cutoff = datetime(2026, 4, 10)
        item = {**SAMPLE_ITEM, "pbanc_rcpt_bgng_dt": "20260401"}
        result = _item_to_notice(item, cutoff)
        assert result is None

    def test_cutoff_filtering_recent_item_passes(self):
        """cutoff 이후 항목 → Notice 반환."""
        cutoff = datetime(2026, 3, 1)
        result = _item_to_notice(SAMPLE_ITEM, cutoff)
        assert result is not None

    def test_status_ongoing(self):
        """rcrt_prgs_yn='Y' → status 'ongoing'."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.status == "ongoing"

    def test_status_closed(self):
        """rcrt_prgs_yn='N' → status 'closed'."""
        item = {**SAMPLE_ITEM, "rcrt_prgs_yn": "N"}
        notice = _item_to_notice(item, self._cutoff())
        assert notice.status == "closed"

    def test_status_missing_rcrt_prgs_yn(self):
        """rcrt_prgs_yn 없음 → status 'closed'."""
        item = {k: v for k, v in SAMPLE_ITEM.items() if k != "rcrt_prgs_yn"}
        notice = _item_to_notice(item, self._cutoff())
        assert notice.status == "closed"

    def test_extra_fields(self):
        """extra 딕셔너리에 추가 필드 포함."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.extra is not None
        assert notice.extra["target"] == "예비창업자"
        assert notice.extra["apply_url"] == "https://apply.example.com"
        assert notice.extra["contact"] == "02-1234-5678"
        assert notice.extra["biz_year"] == "2026"
        assert notice.extra["target_age"] == "만 39세 이하"

    def test_minimal_item(self):
        """최소 필드만 있는 항목도 에러 없이 변환."""
        item = {
            "pbanc_sn": 99999,
            "biz_pbanc_nm": "최소 공고",
            "pbanc_rcpt_bgng_dt": "20260401",
        }
        notice = _item_to_notice(item, self._cutoff())
        assert notice is not None
        assert notice.bid_no == "KSTARTUP-99999"
        assert notice.title == "최소 공고"
        assert notice.organization == "창업진흥원"  # default fallback
        assert notice.url == ""

    def test_missing_fields_no_extra(self):
        """추가 필드 모두 비어있으면 extra는 None."""
        item = {
            "pbanc_sn": 99999,
            "biz_pbanc_nm": "최소 공고",
            "pbanc_rcpt_bgng_dt": "20260401",
        }
        notice = _item_to_notice(item, self._cutoff())
        assert notice.extra is None

    def test_organization_fallback_to_sprv_inst(self):
        """pbanc_ntrp_nm 없으면 sprv_inst에서 가져옴."""
        item = {**SAMPLE_ITEM}
        del item["pbanc_ntrp_nm"]
        item["sprv_inst"] = "과학기술부"
        notice = _item_to_notice(item, self._cutoff())
        assert notice.organization == "과학기술부"

    def test_empty_start_date_not_filtered(self):
        """pbanc_rcpt_bgng_dt 비어있으면 cutoff 필터링하지 않음."""
        item = {**SAMPLE_ITEM, "pbanc_rcpt_bgng_dt": ""}
        cutoff = datetime.now() + timedelta(days=1)
        notice = _item_to_notice(item, cutoff)
        assert notice is not None


# ---------------------------------------------------------------------------
# KstartupCollector init tests
# ---------------------------------------------------------------------------


class TestKstartupCollectorInit:
    """KstartupCollector 초기화 테스트."""

    def test_requires_api_key(self):
        """API 키 없으면 ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DATA_GO_KR_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    KstartupCollector()

    def test_source_name(self):
        collector = KstartupCollector(api_key="test-key")
        assert collector.source_name == "K-Startup"

    def test_env_key(self):
        collector = KstartupCollector(api_key="test-key")
        assert collector._env_key() == "DATA_GO_KR_KEY"

    def test_api_key_from_constructor(self):
        collector = KstartupCollector(api_key="my-key")
        assert collector.api_key == "my-key"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"DATA_GO_KR_KEY": "env-key"}):
            collector = KstartupCollector()
            assert collector.api_key == "env-key"


# ---------------------------------------------------------------------------
# KstartupCollector._fetch mock tests
# ---------------------------------------------------------------------------


class TestKstartupCollectorFetch:
    """KstartupCollector._fetch HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_response(self):
        """단일 페이지 응답 → 올바른 Notice 반환."""
        response_data = _make_api_response([SAMPLE_ITEM], total_count=1)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = KstartupCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=30)
        assert len(notices) == 1
        assert pages == 1
        assert notices[0].title == "테스트 창업지원사업"
        assert notices[0].bid_no == "KSTARTUP-12345"

    @pytest.mark.asyncio
    @respx.mock
    async def test_multi_page_pagination(self):
        """totalCount > perPage → 여러 페이지 요청."""
        item1 = {**SAMPLE_ITEM}
        item2 = {**SAMPLE_ITEM, "pbanc_sn": 12346, "biz_pbanc_nm": "두번째 사업"}

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=_make_api_response([item1], total_count=150))
            else:
                return httpx.Response(200, json=_make_api_response([item2], total_count=150))

        respx.get(API_URL).mock(side_effect=side_effect)

        collector = KstartupCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=30)
        assert call_count == 2
        assert pages == 2
        assert len(notices) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_only_ongoing_param(self):
        """only_ongoing=True일 때 cond[rcrt_prgs_yn::EQ]=Y 파라미터 전송."""
        route = respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=_make_api_response([SAMPLE_ITEM]))
        )

        collector = KstartupCollector(api_key="test-key")
        await collector._fetch(days=30, only_ongoing=True)

        assert route.called
        request = route.calls[0].request
        assert "cond%5Brcrt_prgs_yn%3A%3AEQ%5D=Y" in str(request.url) or "cond[rcrt_prgs_yn::EQ]=Y" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_only_ongoing_false_no_cond_param(self):
        """only_ongoing=False일 때 cond 파라미터 없음."""
        route = respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=_make_api_response([SAMPLE_ITEM]))
        )

        collector = KstartupCollector(api_key="test-key")
        await collector._fetch(days=30, only_ongoing=False)

        assert route.called
        request = route.calls[0].request
        url_str = str(request.url)
        assert "rcrt_prgs_yn" not in url_str

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_data_response(self):
        """빈 data 배열 → 빈 리스트."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=_make_api_response([], total_count=0))
        )

        collector = KstartupCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=30)
        assert notices == []
        assert pages == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_graceful(self):
        """HTTP 에러 → 예외 없이 빈 리스트 반환."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = KstartupCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=30)
        assert notices == []
        assert pages == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_graceful(self):
        """네트워크 에러 → 예외 없이 빈 리스트 반환."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = KstartupCollector(api_key="test-key")
        notices, pages = await collector._fetch(days=30)
        assert notices == []
        assert pages == 0


# ---------------------------------------------------------------------------
# KstartupCollector.health_check mock tests
# ---------------------------------------------------------------------------


class TestKstartupCollectorHealthCheck:
    """health_check HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        """정상 응답 → status 'ok'."""
        response_data = _make_api_response([SAMPLE_ITEM])
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = KstartupCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "K-Startup"
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_http_error(self):
        """HTTP 에러 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = KstartupCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "K-Startup"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_empty_data(self):
        """빈 data 응답 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=_make_api_response([], total_count=0))
        )

        collector = KstartupCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert "빈 응답" in result["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_network_error(self):
        """네트워크 에러 → status 'error'."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = KstartupCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "K-Startup"
