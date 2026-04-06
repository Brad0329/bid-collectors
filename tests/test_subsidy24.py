"""보조금24 수집기(subsidy24.py) 단위 테스트."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import respx

from bid_collectors.subsidy24 import (
    _item_to_notice,
    _is_business_target,
    Subsidy24Collector,
    API_URL,
    DEFAULT_PER_PAGE,
    BUSINESS_KEYWORDS,
)


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

SAMPLE_ITEM = {
    "서비스ID": "SVC000001",
    "서비스명": "중소기업 수출 지원사업",
    "서비스목적요약": "중소기업 수출 역량 강화",
    "지원대상": "중소기업, 소상공인",
    "선정기준": "매출액 기준",
    "지원내용": "<p>수출 컨설팅 및 지원금 제공</p>",
    "신청방법": "온라인 신청",
    "신청기한": "2026-04-30",
    "상세조회URL": "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/SVC000001",
    "소관기관코드": "1234567",
    "소관기관명": "중소벤처기업부",
    "부서명": "수출지원과",
    "조회수": 500,
    "소관기관유형": "중앙행정기관",
    "사용자구분": "기업",
    "서비스분야": "산업·중소기업·에너지",
    "접수기관": "중소벤처기업부",
    "전화문의": "1357",
    "등록일시": "2026-04-01 10:00:00",
    "수정일시": "2026-04-05 10:00:00",
    "지원유형": "현금(감면)",
}


def _make_api_response(
    items: list[dict] | None = None,
    page: int = 1,
    per_page: int = 100,
    match_count: int | None = None,
) -> dict:
    """테스트용 API 응답 생성."""
    if items is None:
        items = [SAMPLE_ITEM]
    if match_count is None:
        match_count = len(items)
    return {
        "page": page,
        "perPage": per_page,
        "totalCount": 1000,
        "currentCount": len(items),
        "matchCount": match_count,
        "data": items,
    }


def _make_error_response(code: int = -1, msg: str = "SERVICE_KEY_ERROR") -> dict:
    """테스트용 API 에러 응답 생성."""
    return {
        "code": code,
        "msg": msg,
        "data": [],
    }


# ---------------------------------------------------------------------------
# _item_to_notice tests
# ---------------------------------------------------------------------------

class TestItemToNotice:
    """_item_to_notice 함수 테스트."""

    def test_full_item_mapping(self):
        """전체 항목 → 올바른 Notice 필드 매핑."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice is not None
        assert notice.source == "보조금24"
        assert notice.title == "중소기업 수출 지원사업"
        assert notice.organization == "중소벤처기업부"
        assert notice.status == "ongoing"
        assert notice.category == "산업·중소기업·에너지"

    def test_bid_no_format(self):
        """bid_no는 'GOV24-{서비스ID}' 형식."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.bid_no == "GOV24-SVC000001"

    def test_url_from_detail_url(self):
        """상세조회URL이 있으면 해당 URL 사용."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.url == "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/SVC000001"
        assert notice.detail_url == "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/SVC000001"

    def test_url_fallback_to_gov_kr(self):
        """상세조회URL이 없으면 gov.kr 기본 URL 생성."""
        item = {**SAMPLE_ITEM, "상세조회URL": ""}
        notice = _item_to_notice(item)
        assert notice.url == "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/SVC000001"
        assert notice.detail_url == ""

    def test_url_fallback_when_key_missing(self):
        """상세조회URL 키 자체가 없으면 gov.kr 기본 URL 생성."""
        item = {k: v for k, v in SAMPLE_ITEM.items() if k != "상세조회URL"}
        notice = _item_to_notice(item)
        assert notice.url == "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/SVC000001"

    def test_content_from_summary_and_support(self):
        """content는 서비스목적요약 + 지원내용(HTML 제거) 합성."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert "중소기업 수출 역량 강화" in notice.content
        assert "수출 컨설팅 및 지원금 제공" in notice.content
        assert "<p>" not in notice.content

    def test_content_only_summary(self):
        """지원내용 없으면 서비스목적요약만."""
        item = {**SAMPLE_ITEM, "지원내용": ""}
        notice = _item_to_notice(item)
        assert "중소기업 수출 역량 강화" in notice.content
        assert "수출 컨설팅" not in notice.content

    def test_content_only_support(self):
        """서비스목적요약 없으면 지원내용만."""
        item = {**SAMPLE_ITEM, "서비스목적요약": ""}
        notice = _item_to_notice(item)
        assert "수출 컨설팅 및 지원금 제공" in notice.content

    def test_end_date_from_deadline(self):
        """신청기한에서 end_date 파싱."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.end_date is not None
        assert str(notice.end_date) == "2026-04-30"

    def test_end_date_empty_deadline(self):
        """신청기한이 비어있으면 end_date None."""
        item = {**SAMPLE_ITEM, "신청기한": ""}
        notice = _item_to_notice(item)
        assert notice.end_date is None

    def test_start_date_is_none(self):
        """start_date는 항상 None."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.start_date is None

    def test_extra_fields(self):
        """extra 딕셔너리에 추가 필드 포함."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.extra is not None
        assert notice.extra["support_type"] == "현금(감면)"
        assert notice.extra["target"] == "중소기업, 소상공인"
        assert notice.extra["selection_criteria"] == "매출액 기준"
        assert notice.extra["apply_method"] == "온라인 신청"
        assert notice.extra["deadline_raw"] == "2026-04-30"
        assert notice.extra["department"] == "수출지원과"
        assert notice.extra["agency_type"] == "중앙행정기관"
        assert notice.extra["user_type"] == "기업"
        assert notice.extra["reception_agency"] == "중소벤처기업부"
        assert notice.extra["phone"] == "1357"
        assert notice.extra["view_count"] == 500

    def test_extra_none_when_all_empty(self):
        """extra 필드가 모두 비어있으면 None."""
        item = {
            "서비스ID": "SVC999",
            "서비스명": "테스트",
            "상세조회URL": "https://example.com",
        }
        notice = _item_to_notice(item)
        assert notice is not None
        assert notice.extra is None

    def test_missing_service_id_returns_none(self):
        """서비스ID 없으면 None 반환."""
        item = {**SAMPLE_ITEM, "서비스ID": ""}
        assert _item_to_notice(item) is None

    def test_missing_service_name_returns_none(self):
        """서비스명 없으면 None 반환."""
        item = {**SAMPLE_ITEM, "서비스명": ""}
        assert _item_to_notice(item) is None

    def test_missing_both_returns_none(self):
        """서비스ID와 서비스명 모두 없으면 None."""
        item = {"지원내용": "test"}
        assert _item_to_notice(item) is None


# ---------------------------------------------------------------------------
# _is_business_target tests
# ---------------------------------------------------------------------------

class TestIsBusinessTarget:
    """_is_business_target 함수 테스트."""

    def test_target_with_기업(self):
        """지원대상에 '기업' 포함 → True."""
        item = {"서비스명": "일반 사업", "지원대상": "중소기업", "사용자구분": "", "서비스분야": ""}
        assert _is_business_target(item) is True

    def test_service_name_with_창업(self):
        """서비스명에 '창업' 포함 → True."""
        item = {"서비스명": "청년 창업 지원", "지원대상": "청년", "사용자구분": "", "서비스분야": ""}
        assert _is_business_target(item) is True

    def test_user_type_with_소상공인(self):
        """사용자구분에 '소상공인' 포함 → True."""
        item = {"서비스명": "테스트", "지원대상": "", "사용자구분": "소상공인", "서비스분야": ""}
        assert _is_business_target(item) is True

    def test_service_field_with_벤처(self):
        """서비스분야에 '벤처' 포함 → True."""
        item = {"서비스명": "지원", "지원대상": "", "사용자구분": "", "서비스분야": "벤처 지원"}
        assert _is_business_target(item) is True

    def test_no_business_keywords(self):
        """비즈니스 키워드 없음 → False."""
        item = {"서비스명": "복지 급여", "지원대상": "저소득층", "사용자구분": "시민", "서비스분야": "복지"}
        assert _is_business_target(item) is False

    def test_empty_item(self):
        """빈 항목 → False."""
        assert _is_business_target({}) is False

    def test_sample_item_is_business(self):
        """SAMPLE_ITEM은 기업 대상."""
        assert _is_business_target(SAMPLE_ITEM) is True


# ---------------------------------------------------------------------------
# Subsidy24Collector init tests
# ---------------------------------------------------------------------------

class TestSubsidy24CollectorInit:
    """Subsidy24Collector 초기화 테스트."""

    def test_requires_api_key(self):
        """API 키 없으면 ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("DATA_GO_KR_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    Subsidy24Collector()

    def test_source_name(self):
        collector = Subsidy24Collector(api_key="test-key")
        assert collector.source_name == "보조금24"

    def test_api_key_from_constructor(self):
        collector = Subsidy24Collector(api_key="my-key")
        assert collector.api_key == "my-key"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"DATA_GO_KR_KEY": "env-key"}):
            collector = Subsidy24Collector()
            assert collector.api_key == "env-key"


# ---------------------------------------------------------------------------
# Subsidy24Collector._fetch mock tests
# ---------------------------------------------------------------------------

class TestSubsidy24CollectorFetch:
    """Subsidy24Collector._fetch HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_response(self):
        """단일 페이지 응답 → 올바른 Notice 반환."""
        response_data = _make_api_response([SAMPLE_ITEM], match_count=1)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        kwargs = {}
        notices, pages = await collector._fetch(days=7, **kwargs)
        assert len(notices) == 1
        assert notices[0].title == "중소기업 수출 지원사업"
        assert notices[0].bid_no == "GOV24-SVC000001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_multi_page_pagination(self):
        """matchCount > perPage → 여러 페이지 요청."""
        item1 = {**SAMPLE_ITEM}
        page1_data = _make_api_response([item1], match_count=150)

        item2 = {**SAMPLE_ITEM, "서비스ID": "SVC000002", "서비스명": "두번째 사업"}
        page2_data = _make_api_response([item2], match_count=150)

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=page1_data)
            else:
                return httpx.Response(200, json=page2_data)

        respx.get(API_URL).mock(side_effect=side_effect)

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=7)
        assert call_count == 2
        assert len(notices) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_error_response(self):
        """API 에러 응답 (code < 0) → 빈 리스트."""
        error_data = _make_error_response(code=-1, msg="SERVICE_KEY_ERROR")
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=error_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_only_business_filter(self):
        """only_business=True → 비즈니스 대상만 반환."""
        biz_item = {**SAMPLE_ITEM}  # 기업 키워드 포함
        citizen_item = {
            **SAMPLE_ITEM,
            "서비스ID": "SVC000099",
            "서비스명": "복지 급여 지원",
            "지원대상": "저소득층 시민",
            "사용자구분": "시민",
            "서비스분야": "복지",
        }
        response_data = _make_api_response([biz_item, citizen_item], match_count=2)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=7, only_business=True)
        assert len(notices) == 1
        assert notices[0].bid_no == "GOV24-SVC000001"

    @pytest.mark.asyncio
    @respx.mock
    async def test_only_business_false_returns_all(self):
        """only_business=False (기본값) → 모든 항목 반환."""
        biz_item = {**SAMPLE_ITEM}
        citizen_item = {
            **SAMPLE_ITEM,
            "서비스ID": "SVC000099",
            "서비스명": "복지 급여 지원",
            "지원대상": "저소득층 시민",
            "사용자구분": "시민",
            "서비스분야": "복지",
        }
        response_data = _make_api_response([biz_item, citizen_item], match_count=2)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=7)
        assert len(notices) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_data(self):
        """빈 data → 빈 리스트."""
        response_data = _make_api_response(items=[], match_count=0)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_graceful(self):
        """HTTP 에러 → 예외 없이 빈 리스트."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_graceful(self):
        """네트워크 에러 → 예외 없이 빈 리스트."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_skips_invalid_items(self):
        """서비스ID/서비스명 없는 항목은 건너뜀."""
        valid_item = {**SAMPLE_ITEM}
        invalid_item = {"서비스ID": "", "서비스명": ""}
        response_data = _make_api_response([valid_item, invalid_item], match_count=2)
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages = await collector._fetch(days=7)
        assert len(notices) == 1


# ---------------------------------------------------------------------------
# Subsidy24Collector.health_check mock tests
# ---------------------------------------------------------------------------

class TestSubsidy24CollectorHealthCheck:
    """health_check HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        """정상 응답 → status 'ok'."""
        response_data = _make_api_response([SAMPLE_ITEM])
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "보조금24"
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_api_error(self):
        """API 에러 응답 → status 'error'."""
        error_data = _make_error_response(code=-1, msg="KEY_ERROR")
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=error_data)
        )

        collector = Subsidy24Collector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "보조금24"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_http_error(self):
        """HTTP 에러 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = Subsidy24Collector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "보조금24"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_network_error(self):
        """네트워크 에러 → status 'error'."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = Subsidy24Collector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "보조금24"
        assert "message" in result
