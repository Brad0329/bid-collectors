"""HTML 텍스트 정리 유틸리티 — lets_portal text.py 이식."""

import html
import re


def clean_html(text: str) -> str:
    """HTML 엔티티 디코딩, <br> → 줄바꿈. 태그는 유지."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return text.strip()


def clean_html_to_text(html_str: str) -> str:
    """HTML 태그 완전 제거, 순수 텍스트 반환.

    - </p>, </div>, </li> → 줄바꿈
    - 연속 공백/줄바꿈 정리
    """
    if not html_str:
        return ""
    text = html.unescape(html_str)
    # 블록 태그 → 줄바꿈
    text = re.sub(r"</?(p|div|li|tr|br)\s*/?>", "\n", text, flags=re.IGNORECASE)
    # 나머지 태그 제거
    text = re.sub(r"<[^>]+>", "", text)
    # 연속 공백 정리
    text = re.sub(r"[ \t]+", " ", text)
    # 연속 줄바꿈 정리 (최대 2줄)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 각 줄 앞뒤 공백 제거
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(lines).strip()
