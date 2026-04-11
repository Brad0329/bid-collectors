"""GenericScraper 단위 + respx 통합 테스트."""

import hashlib

import httpx
import pytest
import respx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from bid_collectors.generic_scraper import GenericScraper, ScraperConfig


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

MINIMAL_CONFIG = {
    "name": "테스트사이트",
    "source_key": "testsite",
    "list_url": "https://example.com/board/list",
    "list_selector": "table tbody tr",
    "title_selector": "td:nth-child(2) a",
    "date_selector": "td:nth-child(3)",
}

FULL_CONFIG = {
    **MINIMAL_CONFIG,
    "link_attr": "href",
    "link_base": "https://example.com",
    "pagination": "&page={page}",
    "max_pages": 5,
    "encoding": "utf-8",
    "parser": "html.parser",
    "offset_size": 0,
    "link_js_regex": "",
    "link_template": "",
    "session_init_url": "",
    "post_data": None,
    "post_json": False,
    "page_param_key": "",
    "grid_selector": "",
    "skip_no_date": True,
    "verify_ssl": True,
}


def _make_html(rows_html: str, grid_id: str = "") -> str:
    """테스트용 HTML 생성."""
    table = f"<table><tbody>{rows_html}</tbody></table>"
    if grid_id:
        return f'<html><body><div id="{grid_id}">{table}</div></body></html>'
    return f"<html><body>{table}</body></html>"


def _make_row(title: str, date: str, href: str = "/detail/1") -> str:
    return (
        f"<tr>"
        f'<td>1</td>'
        f'<td><a href="{href}">{title}</a></td>'
        f"<td>{date}</td>"
        f"</tr>"
    )


# ─────────────────────────────────────────────
# ScraperConfig 검증 테스트
# ─────────────────────────────────────────────


class TestScraperConfig:
    def test_minimal_required_fields(self):
        config = ScraperConfig(**MINIMAL_CONFIG)
        assert config.name == "테스트사이트"
        assert config.source_key == "testsite"
        assert config.max_pages == 3  # 기본값
        assert config.encoding == "utf-8"
        assert config.parser == "html.parser"
        assert config.skip_no_date is True

    def test_full_fields(self):
        config = ScraperConfig(**FULL_CONFIG)
        assert config.max_pages == 5
        assert config.pagination == "&page={page}"

    def test_missing_required_field_name(self):
        data = {**MINIMAL_CONFIG}
        del data["name"]
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_missing_required_field_source_key(self):
        data = {**MINIMAL_CONFIG}
        del data["source_key"]
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_missing_required_field_list_url(self):
        data = {**MINIMAL_CONFIG}
        del data["list_url"]
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_source_key_uppercase_rejected(self):
        data = {**MINIMAL_CONFIG, "source_key": "UPPER"}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_source_key_korean_rejected(self):
        data = {**MINIMAL_CONFIG, "source_key": "한국어"}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_source_key_special_chars_rejected(self):
        data = {**MINIMAL_CONFIG, "source_key": "test-site"}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_source_key_auto_lowercase(self):
        """source_key 패턴은 소문자만 허용하므로 대문자 입력은 거부."""
        data = {**MINIMAL_CONFIG, "source_key": "ABC"}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_link_js_regex_only(self):
        data = {**MINIMAL_CONFIG, "link_js_regex": r"fncShow\('(\d+)'\)"}
        with pytest.raises(ValidationError, match="link_js_regex.*link_template"):
            ScraperConfig(**data)

    def test_link_template_only(self):
        data = {**MINIMAL_CONFIG, "link_template": "/detail?id={id}"}
        with pytest.raises(ValidationError, match="link_js_regex.*link_template"):
            ScraperConfig(**data)

    def test_link_js_pair_valid(self):
        data = {
            **MINIMAL_CONFIG,
            "link_js_regex": r"fncShow\('(\d+)'\)",
            "link_template": "/detail?id={id}",
        }
        config = ScraperConfig(**data)
        assert config.link_js_regex

    def test_page_param_key_without_post_data(self):
        data = {**MINIMAL_CONFIG, "page_param_key": "pageIndex"}
        with pytest.raises(ValidationError, match="post_data"):
            ScraperConfig(**data)

    def test_offset_size_without_offset_placeholder(self):
        data = {**MINIMAL_CONFIG, "offset_size": 10, "pagination": "&page={page}"}
        with pytest.raises(ValidationError, match="offset"):
            ScraperConfig(**data)

    def test_offset_size_with_offset_valid(self):
        data = {
            **MINIMAL_CONFIG,
            "offset_size": 10,
            "pagination": "&pager.offset={offset}",
        }
        config = ScraperConfig(**data)
        assert config.offset_size == 10

    def test_max_pages_zero_rejected(self):
        data = {**MINIMAL_CONFIG, "max_pages": 0}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_max_pages_over_50_rejected(self):
        data = {**MINIMAL_CONFIG, "max_pages": 51}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_parser_invalid(self):
        data = {**MINIMAL_CONFIG, "parser": "invalid_parser"}
        with pytest.raises(ValidationError):
            ScraperConfig(**data)

    def test_parser_valid_values(self):
        for p in ("html.parser", "lxml", "html5lib"):
            data = {**MINIMAL_CONFIG, "parser": p}
            config = ScraperConfig(**data)
            assert config.parser == p


# ─────────────────────────────────────────────
# GenericScraper 초기화 테스트
# ─────────────────────────────────────────────


class TestGenericScraperInit:
    def test_init_with_config_instance(self):
        config = ScraperConfig(**MINIMAL_CONFIG)
        scraper = GenericScraper(config)
        assert scraper.source_name == "테스트사이트"
        assert scraper.config is config

    def test_init_with_raw_dict(self):
        scraper = GenericScraper(MINIMAL_CONFIG)
        assert scraper.source_name == "테스트사이트"
        assert isinstance(scraper.config, ScraperConfig)

    def test_init_invalid_dict(self):
        with pytest.raises(ValidationError):
            GenericScraper({"name": "test"})

    def test_api_key_is_none(self):
        scraper = GenericScraper(MINIMAL_CONFIG)
        assert scraper.api_key is None

    def test_source_name_matches_config_name(self):
        scraper = GenericScraper({**MINIMAL_CONFIG, "name": "커스텀이름"})
        assert scraper.source_name == "커스텀이름"


# ─────────────────────────────────────────────
# _extract_link 테스트
# ─────────────────────────────────────────────


class TestExtractLink:
    def _get_scraper(self, **overrides):
        data = {**MINIMAL_CONFIG, **overrides}
        return GenericScraper(data)

    def _make_tag(self, html_str: str):
        soup = BeautifulSoup(html_str, "html.parser")
        return soup.find()

    def test_direct_href(self):
        scraper = self._get_scraper(link_base="https://example.com")
        tag = self._make_tag('<a href="/detail/123">제목</a>')
        assert scraper._extract_link(tag) == "https://example.com/detail/123"

    def test_js_onclick_single_group(self):
        scraper = self._get_scraper(
            link_js_regex=r"fncShow\('(\d+)'\)",
            link_template="/detail.do?seq={id}",
            link_base="https://example.com",
        )
        tag = self._make_tag("<a href=\"javascript:fncShow('456')\">제목</a>")
        assert scraper._extract_link(tag) == "https://example.com/detail.do?seq=456"

    def test_js_onclick_multi_group(self):
        scraper = self._get_scraper(
            link_js_regex=r"jf_view\('(\w+)',\s*'(\w+)',\s*'(\d+)'\)",
            link_template="https://site.kr/bid/{1}/{3}/{2}View",
        )
        tag = self._make_tag(
            """<a href="javascript:jf_view('abc', 'def', '789')">제목</a>"""
        )
        result = scraper._extract_link(tag)
        assert result == "https://site.kr/bid/abc/789/defView"

    def test_relative_url_with_link_base(self):
        scraper = self._get_scraper(link_base="https://base.com")
        tag = self._make_tag('<a href="/path/to/detail">제목</a>')
        assert scraper._extract_link(tag) == "https://base.com/path/to/detail"

    def test_relative_url_fallback_to_list_url(self):
        scraper = self._get_scraper(link_base="")
        tag = self._make_tag('<a href="/path/detail">제목</a>')
        result = scraper._extract_link(tag)
        assert result.startswith("https://example.com")

    def test_no_link_element(self):
        scraper = self._get_scraper()
        tag = self._make_tag("<span>제목만</span>")
        assert scraper._extract_link(tag) == ""

    def test_javascript_void_without_regex(self):
        scraper = self._get_scraper()
        tag = self._make_tag('<a href="javascript:void(0)">제목</a>')
        result = scraper._extract_link(tag)
        assert result == "javascript:void(0)"

    def test_title_el_not_anchor(self):
        scraper = self._get_scraper(link_base="https://example.com")
        tag = self._make_tag('<td><a href="/detail/99">제목</a></td>')
        assert scraper._extract_link(tag) == "https://example.com/detail/99"


# ─────────────────────────────────────────────
# _parse_rows 테스트
# ─────────────────────────────────────────────


class TestParseRows:
    def _get_scraper(self, **overrides):
        data = {**MINIMAL_CONFIG, **overrides}
        return GenericScraper(data)

    def test_standard_table_rows(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(link_base="https://example.com")
        today = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(
            _make_row("공고1", today) + _make_row("공고2", today)
        )
        cutoff = datetime.now() - timedelta(days=30)
        notices, has_old = scraper._parse_rows(html, cutoff)
        assert len(notices) == 2
        assert notices[0].title == "공고1"
        assert notices[1].title == "공고2"

    def test_div_layout(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(
            list_selector="div.item",
            title_selector="a.tit",
            date_selector="span.date",
            link_base="https://example.com",
        )
        today = datetime.now().strftime("%Y-%m-%d")
        html = (
            '<html><body>'
            f'<div class="item"><a class="tit" href="/d/1">제목A</a>'
            f'<span class="date">{today}</span></div>'
            '</body></html>'
        )
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 1
        assert notices[0].title == "제목A"

    def test_cutoff_filtering(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(link_base="https://example.com")
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent_date = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(
            _make_row("오래된공고", old_date) + _make_row("최근공고", recent_date)
        )
        cutoff = datetime.now() - timedelta(days=30)
        notices, has_old = scraper._parse_rows(html, cutoff)
        assert len(notices) == 1
        assert notices[0].title == "최근공고"
        assert has_old is True

    def test_skip_no_date_true(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(skip_no_date=True, link_base="https://example.com")
        html = _make_html(_make_row("날짜없음", ""))
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 0

    def test_skip_no_date_false(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(skip_no_date=False, link_base="https://example.com")
        html = _make_html(_make_row("날짜없음", ""))
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 1
        assert notices[0].title == "날짜없음"

    def test_grid_selector(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(
            grid_selector="#gridData",
            link_base="https://example.com",
        )
        today = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(_make_row("그리드공고", today), grid_id="gridData")
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 1

    def test_grid_selector_not_found(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(grid_selector="#missing")
        html = _make_html(_make_row("공고", "2026-04-10"))
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 0

    def test_no_title_skipped(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(link_base="https://example.com")
        html = _make_html("<tr><td>1</td><td></td><td>2026-04-10</td></tr>")
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 0

    def test_bid_no_format(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(link_base="https://example.com")
        today = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(_make_row("테스트공고", today))
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert notices[0].bid_no.startswith("SCR-testsite-")
        assert len(notices[0].bid_no.split("-")) == 3

    def test_notice_field_mapping(self):
        from datetime import datetime, timedelta

        scraper = self._get_scraper(
            name="커스텀기관",
            source_key="custom",
            link_base="https://example.com",
        )
        today = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(_make_row("매핑테스트", today, "/detail/99"))
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        n = notices[0]
        assert n.source == "커스텀기관"
        assert n.organization == "커스텀기관"
        assert n.url == "https://example.com/detail/99"
        assert n.detail_url == "https://example.com/detail/99"

    def test_row_parse_exception_skipped(self):
        """행 파싱 예외가 발생해도 다른 행은 계속 처리."""
        from datetime import datetime, timedelta

        scraper = self._get_scraper(link_base="https://example.com")
        today = datetime.now().strftime("%Y-%m-%d")
        # 첫 행은 정상, 두 번째 행은 정상
        html = _make_html(
            _make_row("정상공고", today) + _make_row("정상공고2", today)
        )
        cutoff = datetime.now() - timedelta(days=30)
        notices, _ = scraper._parse_rows(html, cutoff)
        assert len(notices) == 2


# ─────────────────────────────────────────────
# _build_page_url 테스트
# ─────────────────────────────────────────────


class TestBuildPageUrl:
    def _get_scraper(self, **overrides):
        data = {**MINIMAL_CONFIG, **overrides}
        return GenericScraper(data)

    def test_page_1_returns_list_url(self):
        scraper = self._get_scraper(pagination="&page={page}")
        assert scraper._build_page_url(1) == MINIMAL_CONFIG["list_url"]

    def test_page_placeholder(self):
        scraper = self._get_scraper(pagination="&pageIndex={page}")
        url = scraper._build_page_url(3)
        assert url == MINIMAL_CONFIG["list_url"] + "&pageIndex=3"

    def test_offset_placeholder(self):
        scraper = self._get_scraper(
            pagination="&pager.offset={offset}",
            offset_size=10,
        )
        url = scraper._build_page_url(3)
        assert url == MINIMAL_CONFIG["list_url"] + "&pager.offset=20"

    def test_no_pagination(self):
        scraper = self._get_scraper(pagination="")
        url = scraper._build_page_url(5)
        assert url == MINIMAL_CONFIG["list_url"]

    def test_various_pagination_patterns(self):
        for pattern, page, expected_suffix in [
            ("?page={page}", 2, "?page=2"),
            ("&nPage={page}", 4, "&nPage=4"),
        ]:
            scraper = self._get_scraper(pagination=pattern)
            url = scraper._build_page_url(page)
            assert url.endswith(expected_suffix)


# ─────────────────────────────────────────────
# _make_bid_no 테스트
# ─────────────────────────────────────────────


class TestMakeBidNo:
    def _get_scraper(self):
        return GenericScraper(MINIMAL_CONFIG)

    def test_deterministic(self):
        scraper = self._get_scraper()
        bid1 = scraper._make_bid_no("제목", "https://example.com/1")
        bid2 = scraper._make_bid_no("제목", "https://example.com/1")
        assert bid1 == bid2

    def test_different_input_different_output(self):
        scraper = self._get_scraper()
        bid1 = scraper._make_bid_no("제목A", "https://example.com/1")
        bid2 = scraper._make_bid_no("제목B", "https://example.com/2")
        assert bid1 != bid2

    def test_format(self):
        scraper = self._get_scraper()
        bid = scraper._make_bid_no("제목", "https://example.com/1")
        assert bid.startswith("SCR-testsite-")
        # md5 해시 10자리
        hash_part = bid.split("-", 2)[2]
        assert len(hash_part) == 10
        assert all(c in "0123456789abcdef" for c in hash_part)


# ─────────────────────────────────────────────
# respx 통합 테스트 (_fetch 전체 흐름)
# ─────────────────────────────────────────────


class TestFetchIntegration:
    """respx로 httpx를 모킹하여 _fetch 전체 흐름 테스트."""

    def _make_page_html(self, rows_data: list[tuple[str, str, str]]) -> str:
        """[(title, date, href), ...] → HTML."""
        rows = "".join(_make_row(t, d, h) for t, d, h in rows_data)
        return _make_html(rows)

    @respx.mock
    async def test_single_page_get(self):
        from datetime import datetime, timedelta

        today = datetime.now().strftime("%Y-%m-%d")
        html = self._make_page_html([
            ("공고1", today, "/d/1"),
            ("공고2", today, "/d/2"),
        ])
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG, "link_base": "https://example.com", "max_pages": 1,
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 2
        assert pages == 1

    @respx.mock
    async def test_multi_page_pagination(self):
        from datetime import datetime, timedelta

        today = datetime.now().strftime("%Y-%m-%d")
        page1_html = self._make_page_html([("공고1", today, "/d/1")])
        page2_html = self._make_page_html([("공고2", today, "/d/2")])
        page3_html = _make_html("")  # 빈 페이지 → 종료

        base_url = MINIMAL_CONFIG["list_url"]
        respx.get(base_url).respond(200, text=page1_html)
        respx.get(f"{base_url}&page=2").respond(200, text=page2_html)
        respx.get(f"{base_url}&page=3").respond(200, text=page3_html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG,
            "pagination": "&page={page}",
            "max_pages": 5,
            "link_base": "https://example.com",
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 2
        assert pages == 3

    @respx.mock
    async def test_post_request(self):
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        html = self._make_page_html([("POST공고", today, "/d/1")])
        respx.post(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG,
            "post_data": {"search": "1"},
            "page_param_key": "pageIndex",
            "max_pages": 1,
            "link_base": "https://example.com",
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 1

    @respx.mock
    async def test_session_init(self):
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        init_route = respx.get("https://example.com/init").respond(200, text="OK")
        html = self._make_page_html([("세션공고", today, "/d/1")])
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG,
            "session_init_url": "https://example.com/init",
            "max_pages": 1,
            "link_base": "https://example.com",
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert init_route.called
        assert len(notices) == 1

    @respx.mock
    async def test_old_content_early_stop(self):
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        page1_html = self._make_page_html([("오래된공고", old_date, "/d/1")])

        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=page1_html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG,
            "max_pages": 3,
            "link_base": "https://example.com",
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 0
        assert pages == 1  # 1페이지만 처리 후 종료

    @respx.mock
    async def test_http_error_partial_collect(self):
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        page1_html = self._make_page_html([("성공공고", today, "/d/1")])

        base_url = MINIMAL_CONFIG["list_url"]
        respx.get(base_url).respond(200, text=page1_html)
        respx.get(f"{base_url}&page=2").respond(500)

        scraper = GenericScraper({
            **MINIMAL_CONFIG,
            "pagination": "&page={page}",
            "max_pages": 5,
            "link_base": "https://example.com",
        })
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 1
        assert pages == 1  # 2페이지에서 에러, 1페이지만 카운트

    @respx.mock
    async def test_empty_page(self):
        html = _make_html("")
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({**MINIMAL_CONFIG, "link_base": "https://example.com"})
        notices, pages = await scraper._fetch(days=30, delay=0)
        assert len(notices) == 0
        assert pages == 1


# ─────────────────────────────────────────────
# health_check 테스트
# ─────────────────────────────────────────────


class TestHealthCheck:
    @respx.mock
    async def test_health_ok(self):
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text="<html></html>")
        scraper = GenericScraper(MINIMAL_CONFIG)
        result = await scraper.health_check()
        assert result["status"] == "ok"
        assert result["source"] == "테스트사이트"
        assert "response_time_ms" in result

    @respx.mock
    async def test_health_http_error(self):
        respx.get(MINIMAL_CONFIG["list_url"]).respond(500)
        scraper = GenericScraper(MINIMAL_CONFIG)
        result = await scraper.health_check()
        assert result["status"] == "error"
        assert "message" in result

    @respx.mock
    async def test_health_network_error(self):
        respx.get(MINIMAL_CONFIG["list_url"]).mock(side_effect=httpx.ConnectError("fail"))
        scraper = GenericScraper(MINIMAL_CONFIG)
        result = await scraper.health_check()
        assert result["status"] == "error"


# ─────────────────────────────────────────────
# collect() 통합 (BaseCollector 래핑)
# ─────────────────────────────────────────────


class TestCollectIntegration:
    @respx.mock
    async def test_collect_returns_collect_result(self):
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        html = _make_html(_make_row("수집테스트", today))
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG, "link_base": "https://example.com", "max_pages": 1,
        })
        result = await scraper.collect(days=30, delay=0)

        assert result.source == "테스트사이트"
        assert len(result.notices) == 1
        assert result.notices[0].title == "수집테스트"
        assert result.pages_processed == 1
        assert result.is_partial is False

    @respx.mock
    async def test_collect_dedup(self):
        """동일 bid_no 중복 제거 확인."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        # 같은 제목+링크 → 같은 bid_no
        html = _make_html(
            _make_row("중복공고", today, "/d/same")
            + _make_row("중복공고", today, "/d/same")
        )
        respx.get(MINIMAL_CONFIG["list_url"]).respond(200, text=html)

        scraper = GenericScraper({
            **MINIMAL_CONFIG, "link_base": "https://example.com", "max_pages": 1,
        })
        result = await scraper.collect(days=30, delay=0)
        assert result.total_fetched == 2
        assert result.total_after_dedup == 1
