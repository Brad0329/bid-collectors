"""공통 HTTP 클라이언트."""

import httpx

DEFAULT_TIMEOUT = 15.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3

# 커스텀 transport를 넘기면 httpx는 클라이언트 수준의 이 인자들을 무시한다 → transport로 옮겨야 적용된다
_TRANSPORT_KWARGS = ("verify", "cert", "trust_env", "http1", "http2", "limits", "proxy")


def create_client(**kwargs) -> httpx.AsyncClient:
    """공통 설정이 적용된 httpx.AsyncClient 생성."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    transport_kwargs = {k: kwargs.pop(k) for k in _TRANSPORT_KWARGS if k in kwargs}

    transport = httpx.AsyncHTTPTransport(retries=MAX_RETRIES, **transport_kwargs)

    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
        **kwargs,
    )
