"""보조금24 수집기(subsidy24.py) 단위 테스트."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import respx

from bid_collectors.base import MissingFieldError
from bid_collectors.subsidy24 import (
    _item_to_notice,
    _is_business_target,
    Subsidy24Collector,
    API_URL,
)


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

# 픽스처 날짜는 실행 시점 상대값 — 고정 마감일은 시간이 지나면 closed가 되어 깨진다
DEADLINE = (datetime.now() + timedelta(days=25)).strftime("%Y-%m-%d")

SAMPLE_ITEM = {
    "서비스ID": "SVC000001",
    "서비스명": "중소기업 수출 지원사업",
    "서비스목적요약": "중소기업 수출 역량 강화",
    "지원대상": "중소기업, 소상공인",
    "선정기준": "매출액 기준",
    "지원내용": "<p>수출 컨설팅 및 지원금 제공</p>",
    "신청방법": "온라인 신청",
    "신청기한": DEADLINE,
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

    def test_content_is_summary_only(self):
        """v1.6.0 원칙 ② — content = 서비스목적요약 한 필드(종전 + 지원내용 합성). 지원내용은 extra 원문."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.content == SAMPLE_ITEM["서비스목적요약"]
        assert notice.extra["지원내용"] == SAMPLE_ITEM["지원내용"]

    def test_content_only_summary(self):
        """지원내용 없으면 서비스목적요약만."""
        item = {**SAMPLE_ITEM, "지원내용": ""}
        notice = _item_to_notice(item)
        assert "중소기업 수출 역량 강화" in notice.content
        assert "수출 컨설팅" not in notice.content

    def test_content_no_fallback_to_support(self):
        """서비스목적요약이 없으면 빈 값 — 지원내용으로 대체하지 않는다(v1.6.0)."""
        item = {**SAMPLE_ITEM, "서비스목적요약": ""}
        notice = _item_to_notice(item)
        assert notice.content == ""

    def test_end_date_is_period_end(self):
        """v1.6.0 — 신청기한이 기간이면 끝 날짜가 마감일(종전엔 시작일, 부록 A-2)."""
        notice = _item_to_notice({**SAMPLE_ITEM, "신청기한": f"2026.01.01 ~ {DEADLINE}"})
        assert str(notice.end_date) == DEADLINE

    def test_end_date_from_deadline(self):
        """신청기한에서 end_date 파싱."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.end_date is not None
        assert str(notice.end_date) == DEADLINE

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
        """v1.2.5 원칙 ①: extra는 응답 키 전부·원래 이름(한국어 그대로, 영어 별칭 없음), 값 타입 그대로."""
        notice = _item_to_notice(SAMPLE_ITEM)
        assert notice.extra is not None
        assert notice.extra["지원유형"] == "현금(감면)"
        assert notice.extra["지원대상"] == "중소기업, 소상공인"
        assert notice.extra["선정기준"] == "매출액 기준"
        assert notice.extra["신청방법"] == "온라인 신청"
        assert notice.extra["신청기한"] == DEADLINE
        assert notice.extra["부서명"] == "수출지원과"
        assert notice.extra["소관기관유형"] == "중앙행정기관"
        assert notice.extra["사용자구분"] == "기업"
        assert notice.extra["접수기관"] == "중소벤처기업부"
        assert notice.extra["전화문의"] == "1357"
        assert notice.extra["조회수"] == 500
        assert set(notice.extra) == set(SAMPLE_ITEM)
        assert "support_type" not in notice.extra

    def test_minimal_item_extra_is_raw_fields(self):
        """v1.2.5: 추가 필드가 없어도 표준 필드로 옮긴 값이 원문 그대로 extra에 있다(종전엔 None)."""
        item = {
            "서비스ID": "SVC999",
            "서비스명": "테스트",
            "상세조회URL": "https://example.com",
        }
        notice = _item_to_notice(item)
        assert notice is not None
        assert notice.extra == item

    # v1.2.4: 종전엔 None을 돌려 조용히 버렸다 — 이제 예외로 올려 _fetch가 건너뛴 건수·사유를 errors에 싣는다
    def test_missing_service_id_raises(self):
        item = {**SAMPLE_ITEM, "서비스ID": ""}
        with pytest.raises(MissingFieldError, match="서비스ID"):
            _item_to_notice(item)

    def test_missing_service_name_raises(self):
        item = {**SAMPLE_ITEM, "서비스명": ""}
        with pytest.raises(MissingFieldError, match="서비스명"):
            _item_to_notice(item)

    def test_missing_both_raises(self):
        item = {"지원내용": "test"}
        with pytest.raises(MissingFieldError, match="서비스ID,서비스명"):
            _item_to_notice(item)

    # v1.2.5 B — 선택 필드 하나의 이상이 항목(또는 결과 전체)을 잃게 하지 않는다
    def test_null_organization_keeps_item(self):
        """소관기관명이 null이면 종전엔 ValidationError로 건너뛰었다 — 선택 필드라 비운 채 돌아온다."""
        notice = _item_to_notice({**SAMPLE_ITEM, "소관기관명": None})
        assert notice.organization == ""

    @pytest.mark.parametrize("item, expected", [
        ({"서비스명": None, "지원대상": 123, "사용자구분": ["소상공인"], "서비스분야": {"a": 1}}, True),
        ({"서비스명": None, "지원대상": None}, False),
        ({}, False),
    ])
    def test_is_business_target_never_raises(self, item, expected):
        """항목 변환 try 밖에서 불리므로 어떤 값(null·숫자·list·dict)에도 예외를 던지지 않는다."""
        assert _is_business_target(item) is expected


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
        notices, pages, errors = await collector._fetch(days=7, **kwargs)
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
        notices, pages, errors = await collector._fetch(days=7)
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
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []
        assert len(errors) == 1
        assert "API 에러: -1 - SERVICE_KEY_ERROR" in errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_cutoff_truncated_to_midnight(self):
        """cond[수정일시::GTE]는 cutoff 날짜의 00:00:00, API 값과 같은 YYYYMMDDHHMMSS 형식."""
        route = respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=_make_api_response([], match_count=0))
        )
        await Subsidy24Collector(api_key="test-key")._fetch(days=3)
        expected = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d") + "000000"
        assert route.calls[0].request.url.params["cond[수정일시::GTE]"] == expected

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_key_masked_in_errors(self):
        secret = "Ab+c/D==SECRET"
        respx.get(API_URL).mock(return_value=httpx.Response(500))
        result = await Subsidy24Collector(api_key=secret).collect(days=1)
        joined = " ".join(result.errors)
        assert result.errors
        assert "SECRET" not in joined
        assert "serviceKey=***" in joined

    @pytest.mark.asyncio
    @respx.mock
    async def test_max_pages_truncation_reported(self):
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json=_make_api_response(
                [{**SAMPLE_ITEM, "서비스ID": f"SVC{i}"}], match_count=500))
            for i in range(3)
        ])
        result = await Subsidy24Collector(api_key="test-key").collect(days=7, max_pages=2)
        assert route.call_count == 2
        assert len(result.notices) == 2
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "max_pages=2" in result.errors[0]
        assert "500건" in result.errors[0]

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
        notices, pages, errors = await collector._fetch(days=7, only_business=True)
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
        notices, pages, errors = await collector._fetch(days=7)
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
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_graceful(self):
        """HTTP 에러 → 예외 없이 빈 리스트."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []
        assert len(errors) == 1
        assert "페이지 1 요청 실패" in errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_graceful(self):
        """네트워크 에러 → 예외 없이 빈 리스트 + errors에 원인."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = Subsidy24Collector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []
        assert len(errors) == 1
        assert "ConnectError" in errors[0]

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
        notices, pages, errors = await collector._fetch(days=7)
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
