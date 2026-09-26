"""기업마당 수집기(bizinfo.py) 단위 테스트."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import respx

from bid_collectors.bizinfo import (
    _is_within_cutoff,
    _item_to_notice,
    _parse_attachments,
    BizinfoCollector,
    API_URL,
)


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

# 픽스처 날짜는 실행 시점 상대값 — 고정 날짜는 cutoff(실행 시점 상대)에 걸려 시간이 지나면 깨진다
START = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
END = (datetime.now() + timedelta(days=25)).strftime("%Y-%m-%d")
CREATED = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d 10:00:00")

SAMPLE_ITEM = {
    "pblancId": "PBLN_000000000120389",
    "pblancNm": "[경기] 테스트 지원사업 공고",
    "pblancUrl": "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000120389",
    "excInsttNm": "테스트진흥원",
    "jrsdInsttNm": "경기도",
    "trgetNm": "중소기업",
    "reqstBeginEndDe": f"{START} ~ {END}",
    "bsnsSumryCn": "<p>테스트 사업 내용입니다</p>",
    "pldirSportRealmLclasCodeNm": "수출",
    "pldirSportRealmMlsfcCodeNm": "수출정보제공",
    "hashtags": "수출,경영",
    "refrncNm": "담당자 070-1234-5678",
    "reqstMthPapersCn": "이메일 접수",
    "inqireCo": 100,
    "creatPnttm": CREATED,
    "updtPnttm": CREATED,
    "totCnt": 1,
    "printFileNm": "공고문.hwp",
    "printFlpthNm": "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=FILE1",
    "fileNm": "포스터.jpg",
    "flpthNm": "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=FILE2",
}


def _make_json_response(items: list[dict] | None = None, tot_cnt: int | None = None) -> dict:
    """테스트용 JSON 응답 생성."""
    if items is None:
        items = [SAMPLE_ITEM]
    if tot_cnt is not None:
        for item in items:
            item = {**item, "totCnt": tot_cnt}
    return {"jsonArray": items}


# ---------------------------------------------------------------------------
# _is_within_cutoff tests
# ---------------------------------------------------------------------------

class TestIsWithinCutoff:
    """_is_within_cutoff 함수 테스트."""

    def test_recent_date_returns_true(self):
        """최근 날짜 → True."""
        cutoff = datetime.now() - timedelta(days=7)
        item = {"creatPnttm": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        assert _is_within_cutoff(item, cutoff) is True

    def test_old_date_returns_false(self):
        """오래된 날짜 → False."""
        cutoff = datetime.now() - timedelta(days=1)
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        item = {"creatPnttm": old_date}
        assert _is_within_cutoff(item, cutoff) is False

    def test_empty_date_returns_true(self):
        """빈 날짜 → True (안전 기본값)."""
        cutoff = datetime.now() - timedelta(days=1)
        assert _is_within_cutoff({"creatPnttm": ""}, cutoff) is True
        assert _is_within_cutoff({}, cutoff) is True

    def test_invalid_date_returns_true(self):
        """잘못된 날짜 형식 → True (안전 기본값)."""
        cutoff = datetime.now() - timedelta(days=1)
        item = {"creatPnttm": "not-a-date"}
        assert _is_within_cutoff(item, cutoff) is True

    def test_exact_cutoff_date_returns_true(self):
        """정확히 cutoff 날짜 → True (>= 비교)."""
        cutoff = datetime(2026, 4, 5)
        item = {"creatPnttm": "2026-04-05 00:00:00"}
        assert _is_within_cutoff(item, cutoff) is True


# ---------------------------------------------------------------------------
# _parse_attachments tests
# ---------------------------------------------------------------------------

class TestParseAttachments:
    """_parse_attachments 함수 테스트."""

    def test_both_files_present(self):
        """printFile + file 모두 있음 → 2개 첨부."""
        result = _parse_attachments(SAMPLE_ITEM)
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "공고문.hwp"
        assert result[0]["url"].endswith("FILE1")
        assert result[1]["name"] == "포스터.jpg"
        assert result[1]["url"].endswith("FILE2")

    def test_only_print_file(self):
        """printFile만 있음 → 1개 첨부."""
        item = {
            "printFileNm": "공고문.pdf",
            "printFlpthNm": "https://example.com/file1",
        }
        result = _parse_attachments(item)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "공고문.pdf"

    def test_only_second_file(self):
        """fileNm만 있음 → 1개 첨부."""
        item = {
            "fileNm": "첨부.zip",
            "flpthNm": "https://example.com/file2",
        }
        result = _parse_attachments(item)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "첨부.zip"

    def test_no_files(self):
        """첨부파일 없음 → None."""
        result = _parse_attachments({})
        assert result is None

    def test_name_without_url_ignored(self):
        """파일명만 있고 URL 없으면 무시."""
        item = {"printFileNm": "공고문.pdf", "printFlpthNm": ""}
        result = _parse_attachments(item)
        assert result is None


# ---------------------------------------------------------------------------
# _item_to_notice tests
# ---------------------------------------------------------------------------

class TestItemToNotice:
    """_item_to_notice 함수 테스트."""

    def _cutoff(self) -> datetime:
        return datetime.now() - timedelta(days=7)

    def test_full_item_mapping(self):
        """전체 항목 → 올바른 Notice 필드 매핑."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice is not None
        assert notice.source == "기업마당"
        assert notice.title == "[경기] 테스트 지원사업 공고"
        assert notice.organization == "테스트진흥원"
        assert notice.region == ""  # v1.6.0 원칙 ② — jrsdInsttNm은 소관기관이지 지역이 아니다(extra 원문)
        assert notice.extra["jrsdInsttNm"] == "경기도"
        assert notice.category == "수출"

    def test_bid_no_format(self):
        """bid_no는 'BIZINFO-{pblancId}' 형식."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.bid_no == "BIZINFO-PBLN_000000000120389"

    def test_url_from_pblanc_url(self):
        """URL은 pblancUrl에서 가져옴."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.url == SAMPLE_ITEM["pblancUrl"]
        assert notice.detail_url == SAMPLE_ITEM["pblancUrl"]

    def test_content_html_cleaned(self):
        """bsnsSumryCn의 HTML이 정리됨."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert "<p>" not in notice.content
        assert "테스트 사업 내용입니다" in notice.content

    def test_date_parsing_period_format(self):
        """reqstBeginEndDe 기간 형식 파싱 (시작일 ~ 종료일)."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.start_date is not None
        assert str(notice.start_date) == START
        assert notice.end_date is not None
        assert str(notice.end_date) == END

    def test_single_date_period_is_not_start_date(self):
        """v1.6.0 — 기간이 아닌 날짜 하나는 시작인지 마감인지 모른다 → start·end 둘 다 None(종전엔 start_date)."""
        notice = _item_to_notice({**SAMPLE_ITEM, "reqstBeginEndDe": END}, self._cutoff())
        assert (notice.start_date, notice.end_date) == (None, None)

    def test_attachments_included(self):
        """첨부파일이 Notice에 포함."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.attachments is not None
        assert len(notice.attachments) == 2

    def test_extra_fields(self):
        """v1.2.5 원칙 ①: extra는 응답 키 전부·원래 이름(영어 별칭 없음), 값 타입 그대로."""
        notice = _item_to_notice(SAMPLE_ITEM, self._cutoff())
        assert notice.extra is not None
        assert notice.extra["pldirSportRealmMlsfcCodeNm"] == "수출정보제공"
        assert notice.extra["trgetNm"] == "중소기업"
        assert notice.extra["hashtags"] == "수출,경영"
        assert notice.extra["refrncNm"] == "담당자 070-1234-5678"
        assert notice.extra["reqstMthPapersCn"] == "이메일 접수"
        assert notice.extra["inqireCo"] == 100
        assert set(notice.extra) == set(SAMPLE_ITEM)  # 빈 값이 없는 표본이라 키 집합이 같다
        assert "sub_category" not in notice.extra

    def test_old_item_returns_none(self):
        """cutoff 이전 항목 → None."""
        cutoff = datetime.now() + timedelta(days=1)  # 미래 cutoff → 모든 항목이 old
        old_item = {**SAMPLE_ITEM, "creatPnttm": "2020-01-01 00:00:00"}
        result = _item_to_notice(old_item, cutoff)
        assert result is None

    def test_empty_creatpnttm_not_filtered(self):
        """creatPnttm이 비어있으면 필터링하지 않음."""
        item = {**SAMPLE_ITEM, "creatPnttm": ""}
        cutoff = datetime.now() - timedelta(days=1)
        notice = _item_to_notice(item, cutoff)
        assert notice is not None

    def test_minimal_item(self):
        """최소 필드만 있는 항목도 에러 없이 변환."""
        item = {
            "pblancId": "PBLN_TEST",
            "pblancNm": "최소 공고",
            "pblancUrl": "https://example.com",
            "excInsttNm": "기관",
            "creatPnttm": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        notice = _item_to_notice(item, self._cutoff())
        assert notice is not None
        assert notice.bid_no == "BIZINFO-PBLN_TEST"
        assert notice.attachments is None
        assert set(notice.extra) == set(item)  # v1.2.5: 표준 필드로 옮긴 값도 원문 그대로 extra에(종전엔 None)

    # v1.2.5 B — 선택 필드 하나의 이상이 항목(또는 결과 전체)을 잃게 하지 않는다
    def test_null_reqst_period_keeps_item(self):
        """reqstBeginEndDe가 null이어도 항목은 돌아온다 — 종전엔 `"~" in None` TypeError로 버려졌다."""
        notice = _item_to_notice({**SAMPLE_ITEM, "reqstBeginEndDe": None}, self._cutoff())
        assert notice is not None
        assert notice.start_date is None and notice.end_date is None

    @pytest.mark.parametrize("value", [None, 123, ["x"], {"a": 1}, "", "날짜아님"])
    def test_is_within_cutoff_never_raises(self, value):
        """항목 try 밖(`items[-3:]` 조기 종료 판정)에서 불리므로 어떤 값에도 예외를 던지지 않는다 — 판정 불가는 True(멈추지 않음)."""
        assert _is_within_cutoff({"creatPnttm": value}, self._cutoff()) is True


# ---------------------------------------------------------------------------
# BizinfoCollector init tests
# ---------------------------------------------------------------------------

class TestBizinfoCollectorInit:
    """BizinfoCollector 초기화 테스트."""

    def test_requires_api_key(self):
        """API 키 없으면 ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("BIZINFO_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="API 키가 필요합니다"):
                    BizinfoCollector()

    def test_source_name(self):
        collector = BizinfoCollector(api_key="test-key")
        assert collector.source_name == "기업마당"

    def test_env_key(self):
        collector = BizinfoCollector(api_key="test-key")
        assert collector._env_key() == "BIZINFO_API_KEY"

    def test_api_key_from_constructor(self):
        collector = BizinfoCollector(api_key="my-key")
        assert collector.api_key == "my-key"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"BIZINFO_API_KEY": "env-key"}):
            collector = BizinfoCollector()
            assert collector.api_key == "env-key"


# ---------------------------------------------------------------------------
# BizinfoCollector._fetch mock tests
# ---------------------------------------------------------------------------

class TestBizinfoCollectorFetch:
    """BizinfoCollector._fetch HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_response(self):
        """단일 페이지 응답 → 올바른 Notice 반환."""
        response_data = {"jsonArray": [SAMPLE_ITEM]}
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = BizinfoCollector(api_key="test-key")
        kwargs = {}
        notices, pages, errors = await collector._fetch(days=7, **kwargs)
        assert len(notices) == 1
        assert notices[0].title == "[경기] 테스트 지원사업 공고"
        assert notices[0].bid_no == "BIZINFO-PBLN_000000000120389"

    @pytest.mark.asyncio
    @respx.mock
    async def test_multi_page_pagination(self):
        """totCnt > pageUnit → 여러 페이지 요청."""
        # 첫 페이지: totCnt=150으로 설정
        item1 = {**SAMPLE_ITEM, "totCnt": 150}
        page1_data = {"jsonArray": [item1]}

        # 두 번째 페이지
        item2 = {**SAMPLE_ITEM, "pblancId": "PBLN_000000000120390", "totCnt": 150}
        page2_data = {"jsonArray": [item2]}

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=page1_data)
            else:
                return httpx.Response(200, json=page2_data)

        respx.get(API_URL).mock(side_effect=side_effect)

        collector = BizinfoCollector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=7)
        assert call_count == 2
        assert len(notices) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_json_array(self):
        """빈 jsonArray → 빈 리스트."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json={"jsonArray": []})
        )

        collector = BizinfoCollector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_json_array_key(self):
        """jsonArray 키 없음 → 빈 리스트."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json={})
        )

        collector = BizinfoCollector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_graceful(self):
        """HTTP 에러 → 예외 없이 빈 리스트 반환."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = BizinfoCollector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []
        assert len(errors) == 1
        assert "페이지 1 요청 실패" in errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_graceful(self):
        """네트워크 에러 → 예외 없이 빈 리스트 + errors에 원인."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = BizinfoCollector(api_key="test-key")
        notices, pages, errors = await collector._fetch(days=1)
        assert notices == []
        assert len(errors) == 1
        assert "ConnectError" in errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_second_page_failure_keeps_first_page(self):
        """2페이지 실패 → 1페이지 결과 보존 + is_partial."""
        page1 = {"jsonArray": [{**SAMPLE_ITEM, "totCnt": 150}]}
        respx.get(API_URL).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(500)])

        result = await BizinfoCollector(api_key="test-key").collect(days=7)
        assert len(result.notices) == 1
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "페이지 2 요청 실패" in result.errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_max_pages_truncation_reported(self):
        """max_pages 상한에서 멈추면 잘렸다는 사실과 전체 건수를 알린다."""
        route = respx.get(API_URL).mock(side_effect=[
            httpx.Response(200, json={"jsonArray": [{**SAMPLE_ITEM, "pblancId": f"P{i}", "totCnt": 350}]})
            for i in range(3)
        ])

        result = await BizinfoCollector(api_key="test-key").collect(days=7, max_pages=2)
        assert route.call_count == 2
        assert len(result.notices) == 2
        assert result.is_partial is True
        assert len(result.errors) == 1
        assert "max_pages=2" in result.errors[0]
        assert "350건" in result.errors[0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_key_masked_in_errors(self):
        """httpx 예외 문자열의 URL에 든 키가 errors로 새지 않는다(원문·URL 인코딩 모두)."""
        secret = "Ab+c/D==SECRET"
        respx.get(API_URL).mock(return_value=httpx.Response(500))

        result = await BizinfoCollector(api_key=secret).collect(days=1)
        assert len(result.errors) == 1
        joined = " ".join(result.errors)
        assert "SECRET" not in joined
        assert "crtfcKey=***" in joined


# ---------------------------------------------------------------------------
# BizinfoCollector.health_check mock tests
# ---------------------------------------------------------------------------

class TestBizinfoCollectorHealthCheck:
    """health_check HTTP 모킹 테스트."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_ok(self):
        """정상 응답 → status 'ok'."""
        response_data = {"jsonArray": [SAMPLE_ITEM]}
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json=response_data)
        )

        collector = BizinfoCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "기업마당"
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_http_error(self):
        """HTTP 에러 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(500)
        )

        collector = BizinfoCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "기업마당"
        assert "message" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_empty_response(self):
        """빈 jsonArray 응답 → status 'error'."""
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, json={"jsonArray": []})
        )

        collector = BizinfoCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert "빈 응답" in result["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_check_network_error(self):
        """네트워크 에러 → status 'error'."""
        respx.get(API_URL).mock(side_effect=httpx.ConnectError("connection refused"))

        collector = BizinfoCollector(api_key="test-key")
        result = await collector.health_check()
        assert result["status"] == "error"
        assert result["source"] == "기업마당"
