"""clean_html(), clean_html_to_text() 단위 테스트."""

import pytest
from bid_collectors.utils.text import clean_html, clean_html_to_text


class TestCleanHtml:
    """clean_html: 엔티티 디코딩 + <br> → 줄바꿈, 태그 유지."""

    def test_html_entity_decoding(self):
        assert clean_html("&amp; &lt; &gt;") == "& < >"

    def test_br_to_newline(self):
        assert clean_html("줄1<br>줄2") == "줄1\n줄2"

    def test_br_self_closing(self):
        assert clean_html("줄1<br/>줄2") == "줄1\n줄2"

    def test_br_with_space(self):
        assert clean_html("줄1<br />줄2") == "줄1\n줄2"

    def test_br_case_insensitive(self):
        assert clean_html("줄1<BR>줄2") == "줄1\n줄2"

    def test_other_tags_preserved(self):
        result = clean_html("<b>bold</b>")
        assert "<b>" in result and "</b>" in result

    def test_empty_string(self):
        assert clean_html("") == ""

    def test_none_returns_empty(self):
        assert clean_html(None) == ""

    def test_strips_whitespace(self):
        assert clean_html("  hello  ") == "hello"


class TestCleanHtmlToText:
    """clean_html_to_text: 태그 완전 제거, 순수 텍스트."""

    def test_removes_all_tags(self):
        result = clean_html_to_text("<p>hello</p><div>world</div>")
        assert "<" not in result
        assert "hello" in result
        assert "world" in result

    def test_block_tags_become_newlines(self):
        result = clean_html_to_text("<p>단락1</p><p>단락2</p>")
        assert "단락1" in result
        assert "단락2" in result

    def test_entity_decoded(self):
        result = clean_html_to_text("<p>&amp; hello</p>")
        assert "& hello" in result

    def test_consecutive_whitespace_collapsed(self):
        result = clean_html_to_text("<p>hello     world</p>")
        assert "hello world" in result

    def test_max_two_consecutive_newlines(self):
        result = clean_html_to_text("<p>a</p><p></p><p></p><p></p><p>b</p>")
        assert "\n\n\n" not in result

    def test_empty_string(self):
        assert clean_html_to_text("") == ""

    def test_none_returns_empty(self):
        assert clean_html_to_text(None) == ""

    def test_inline_tags_removed(self):
        result = clean_html_to_text("<span>hello</span> <strong>world</strong>")
        assert "hello" in result
        assert "world" in result
        assert "<span>" not in result
