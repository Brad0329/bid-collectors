"""determine_status() 단위 테스트."""

import pytest
from datetime import date, timedelta
from bid_collectors.utils.status import determine_status


class TestDetermineStatus:
    """마감일 기준 ongoing/closed 판정."""

    def test_future_date_is_ongoing(self):
        future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert determine_status(future) == "ongoing"

    def test_today_is_ongoing(self):
        today = date.today().strftime("%Y-%m-%d")
        assert determine_status(today) == "ongoing"

    def test_past_date_is_closed(self):
        past = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert determine_status(past) == "closed"

    def test_far_past_is_closed(self):
        assert determine_status("2020-01-01") == "closed"


class TestDetermineStatusEdgeCases:
    """엣지 케이스: 빈 값, None, 잘못된 형식."""

    def test_none_returns_ongoing(self):
        assert determine_status(None) == "ongoing"

    def test_empty_string_returns_ongoing(self):
        assert determine_status("") == "ongoing"

    def test_invalid_format_returns_ongoing(self):
        assert determine_status("not-a-date") == "ongoing"

    def test_wrong_format_returns_ongoing(self):
        assert determine_status("28/03/2024") == "ongoing"

    def test_custom_format(self):
        future = (date.today() + timedelta(days=10)).strftime("%d/%m/%Y")
        assert determine_status(future, date_format="%d/%m/%Y") == "ongoing"
