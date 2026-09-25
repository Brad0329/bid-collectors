"""raw_fields — 응답 항목의 비어 있지 않은 필드 전부를 원래 이름 그대로 (v1.2.5, 원칙 ①)."""

import pytest
from lxml import etree

from bid_collectors.base import raw_fields


class TestRawFields:
    def test_json_keeps_every_nonempty_field_and_drops_empties(self):
        item = {"a": "x", "zero": 0, "false": False, "empty": "", "blank": "  ", "none": None,
                "nested": {"k": None, "v": 1}, "list": [1, ""]}
        assert raw_fields(item) == {
            "a": "x", "zero": 0, "false": False,
            "nested": {"k": None, "v": 1}, "list": [1, ""],  # 중첩 값은 손대지 않는다
        }

    def test_json_all_empty_is_none(self):
        assert raw_fields({"a": "", "b": None, "c": " "}) is None

    def test_xml_text_repeat_and_nested(self):
        el = etree.fromstring(
            "<item><a> x </a><zero>0</zero><empty></empty><blank>  </blank>"
            "<f>1</f><f>2</f><n><k>v</k><k2/></n><!-- 주석 --></item>"
        )
        assert raw_fields(el) == {"a": "x", "zero": "0", "f": ["1", "2"], "n": {"k": "v"}}

    def test_xml_three_repeats_extend_list(self):
        el = etree.fromstring("<item><f>1</f><f>2</f><f>3</f></item>")
        assert raw_fields(el) == {"f": ["1", "2", "3"]}

    def test_xml_all_empty_is_none(self):
        assert raw_fields(etree.fromstring("<item><a/><b> </b></item>")) is None

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            raw_fields("text")
