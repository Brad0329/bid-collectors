"""create_client() 단위 테스트."""

import ssl

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


def _ssl_context(client: httpx.AsyncClient) -> ssl.SSLContext:
    # 커스텀 transport를 쓰므로 실제 요청에 쓰이는 SSL 설정은 transport의 커넥션 풀에 있다
    return client._transport._pool._ssl_context


class TestCreateClientEventHooks:
    """요청 검사 훅(SSRF 방어 등)을 event_hooks로 넘길 수 있다."""

    def test_event_hooks_passed_through(self):
        async def guard(request):
            return None

        client = create_client(event_hooks={"request": [guard]})
        assert client.event_hooks["request"] == [guard]


class TestCreateClientSSL:
    """verify가 클라이언트가 아니라 실제 transport까지 전달되는지 (네트워크 없음)."""

    def test_verify_false_disables_certificate_check(self):
        ctx = _ssl_context(create_client(verify=False))
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    def test_verify_default_checks_certificate(self):
        ctx = _ssl_context(create_client())
        assert ctx.verify_mode == ssl.CERT_REQUIRED
