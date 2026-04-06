"""공통 HTTP 클라이언트."""

import httpx

DEFAULT_TIMEOUT = 15.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3


def create_client(**kwargs) -> httpx.AsyncClient:
    """공통 설정이 적용된 httpx.AsyncClient 생성."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)

    transport = httpx.AsyncHTTPTransport(retries=MAX_RETRIES)

    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
        **kwargs,
    )
