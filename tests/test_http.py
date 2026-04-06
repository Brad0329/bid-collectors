"""create_client() 단위 테스트."""

import pytest
import httpx
from bid_collectors.utils.http import create_client, DEFAULT_USER_AGENT, DEFAULT_TIMEOUT


class TestCreateClient:
    """create_client가 올바른 AsyncClient를 반환하는지 확인."""

    def test_returns_async_client(self):
        client = create_client()
        assert isinstance(client, httpx.AsyncClient)

    def test_default_user_agent(self):
        client = create_client()
        assert client.headers["User-Agent"] == DEFAULT_USER_AGENT

    def test_custom_user_agent(self):
        custom_ua = "CustomBot/1.0"
        client = create_client(headers={"User-Agent": custom_ua})
        assert client.headers["User-Agent"] == custom_ua

    def test_default_timeout(self):
        client = create_client()
        assert client.timeout.connect == DEFAULT_TIMEOUT

    def test_custom_timeout(self):
        client = create_client(timeout=30.0)
        assert client.timeout.connect == 30.0

    def test_follow_redirects_enabled(self):
        client = create_client()
        assert client.follow_redirects is True

    def test_additional_headers_preserved(self):
        client = create_client(headers={"X-Custom": "test"})
        assert client.headers["X-Custom"] == "test"
        # User-Agent도 기본값 적용
        assert client.headers["User-Agent"] == DEFAULT_USER_AGENT
