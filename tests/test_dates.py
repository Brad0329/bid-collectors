"""parse_date() 단위 테스트."""

import pytest
from bid_collectors.utils.dates import parse_date


class TestParseDateStandardFormats:
    """표준 날짜 형식 파싱 테스트."""

    def test_yyyy_mm_dd_dash(self):
        assert parse_date("2024-03-28") == "2024-03-28"

    def test_yyyy_mm_dd_dot(self):
        assert parse_date("2024.03.28") == "2024-03-28"

    def test_yyyy_mm_dd_slash(self):
        assert parse_date("2024/03/28") == "2024-03-28"

    def test_single_digit_month_day(self):
        assert parse_date("2024-3-5") == "2024-03-05"

    def test_compact_yyyymmdd(self):
        assert parse_date("20240328") == "2024-03-28"

    def test_compact_yyyymmddhhmm(self):
        assert parse_date("202403281400") == "2024-03-28"

    def test_short_year(self):
        assert parse_date("24-03-28") == "2024-03-28"

    def test_korean_format(self):
        assert parse_date("2024년 3월 28일") == "2024-03-28"

    def test_korean_format_no_space(self):
        assert parse_date("2024년3월28일") == "2024-03-28"


class TestParseDateRange:
    """기간 형식 파싱 테스트 — 시작일 반환."""

    def test_range_dash(self):
        assert parse_date("2024-03-28 ~ 2024-04-05") == "2024-03-28"

    def test_range_dot(self):
        assert parse_date("2024.03.28 ~ 2024.04.05") == "2024-03-28"

    def test_range_slash(self):
        assert parse_date("2024/03/28 ~ 2024/04/05") == "2024-03-28"


class TestParseDateEdgeCases:
    """엣지 케이스 테스트."""

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert parse_date("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_date("   ") is None

    def test_no_date_returns_none(self):
        assert parse_date("날짜 없음") is None

    def test_invalid_date_returns_none(self):
        """존재하지 않는 날짜 (2월 30일)."""
        assert parse_date("2024-02-30") is None

    def test_invalid_month_returns_none(self):
        assert parse_date("2024-13-01") is None

    def test_surrounding_text(self):
        """앞뒤 텍스트가 있어도 날짜 추출."""
        assert parse_date("공고일: 2024-03-28 입니다") == "2024-03-28"
