"""Notice, CollectResult 모델 단위 테스트."""

import pytest
from datetime import date, datetime
from pydantic import ValidationError
from bid_collectors.models import Notice, CollectResult


class TestNotice:
    """Notice 모델 필수/선택 필드 테스트."""

    def _minimal_notice(self, **kwargs):
        defaults = {
            "source": "test",
            "bid_no": "BID-001",
            "title": "테스트 공고",
            "organization": "테스트 기관",
            "url": "https://example.com",
        }
        defaults.update(kwargs)
        return Notice(**defaults)

    def test_minimal_required_fields(self):
        notice = self._minimal_notice()
        assert notice.source == "test"
        assert notice.bid_no == "BID-001"
        assert notice.title == "테스트 공고"
        assert notice.organization == "테스트 기관"
        assert notice.url == "https://example.com"

    def test_default_values(self):
        notice = self._minimal_notice()
        assert notice.status == "ongoing"
        assert notice.detail_url == ""
        assert notice.content == ""
        assert notice.budget is None
        assert notice.start_date is None
        assert notice.end_date is None
        assert notice.attachments is None
        assert notice.extra is None

    def test_optional_fields(self):
        notice = self._minimal_notice(
            start_date="2024-03-01",
            end_date="2024-04-01",
            status="closed",
            budget=1000000,
            region="서울",
            category="용역",
        )
        assert notice.start_date == date(2024, 3, 1)
        assert notice.end_date == date(2024, 4, 1)
        assert notice.status == "closed"
        assert notice.budget == 1000000

    def test_attachments_list(self):
        attachments = [{"name": "파일.pdf", "url": "https://example.com/file.pdf"}]
        notice = self._minimal_notice(attachments=attachments)
        assert len(notice.attachments) == 1
        assert notice.attachments[0]["name"] == "파일.pdf"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            Notice(source="test", bid_no="001", title="제목")
            # organization, url 누락


class TestCollectResult:
    """CollectResult 모델 테스트."""

    def _make_notice(self):
        return Notice(
            source="test",
            bid_no="BID-001",
            title="테스트",
            organization="기관",
            url="https://example.com",
        )

    def test_creation(self):
        result = CollectResult(
            notices=[self._make_notice()],
            source="test",
            collected_at=datetime.now(),
            duration_seconds=1.5,
            total_fetched=1,
            total_after_dedup=1,
            pages_processed=1,
        )
        assert len(result.notices) == 1
        assert result.source == "test"
        assert result.duration_seconds == 1.5

    def test_default_errors(self):
        result = CollectResult(
            notices=[],
            source="test",
            collected_at=datetime.now(),
            duration_seconds=0.1,
            total_fetched=0,
            total_after_dedup=0,
            pages_processed=0,
        )
        assert result.errors == []
        assert result.is_partial is False

    def test_with_errors(self):
        result = CollectResult(
            notices=[],
            source="test",
            collected_at=datetime.now(),
            duration_seconds=0.5,
            total_fetched=0,
            total_after_dedup=0,
            pages_processed=0,
            errors=["API 오류"],
            is_partial=True,
        )
        assert len(result.errors) == 1
        assert result.is_partial is True
