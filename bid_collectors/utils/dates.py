"""날짜 파서 — lets_portal _parse_date + format_date 통합."""

import re
from datetime import datetime


# 날짜 패턴 (우선순위 순)
_PATTERNS = [
    # 기간 형식: 2024-03-28 ~ 2024-04-05 → 시작일 반환
    # 구분자 뒤 공백 허용 — 보조금24 신청기한 "2026. 2. 1. ~ 2026. 11. 30."(v1.6.0 QA 실측 116건 중 5건이 None이었다)
    (
        re.compile(
            r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?\s*~\s*(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})"
        ),
        "range",
    ),
    # yyyy-MM-dd, yyyy.MM.dd, yyyy/MM/dd, yyyy. M. d.
    (re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})"), "ymd"),
    # yyyyMMdd (8자리)
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), "ymd_compact"),
    # yyyyMMddHHmm (12자리)
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)"), "ymd_hm"),
    # yy-MM-dd
    (re.compile(r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})"), "short_ymd"),
    # 2024년 3월 28일
    (re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"), "korean"),
]


def split_period(text: str) -> tuple[str | None, str | None] | None:
    """기간 문자열 "A ~ B"를 (시작, 끝) 'yyyy-MM-dd'로 나눈다. `~`가 없으면 None(기간이 아니다).

    v1.6.0 원칙 ② — 기간에서 마감일을 뽑을 때 쓴다. `parse_date`는 기간이면 **시작일**을 돌려주므로 마감일 칸에 쓰면 안 된다.
    """
    if not text or "~" not in text:
        return None
    head, _, tail = text.partition("~")
    return parse_date(head), parse_date(tail)


def parse_date(text: str) -> str | None:
    """날짜 문자열을 'yyyy-MM-dd' 형식으로 정규화.

    지원 패턴:
        - 2024-03-28, 2024.03.28, 2024/03/28
        - 20240328
        - 202403281400 (yyyyMMddHHmm)
        - 24-03-28
        - 2024년 3월 28일
        - 2024-03-28 ~ 2024-04-05 (기간 → 시작일)

    Returns:
        'yyyy-MM-dd' 문자열 또는 매칭 실패 시 None
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    for pattern, kind in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue

        try:
            if kind == "range":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "ymd" or kind == "ymd_compact" or kind == "korean":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "ymd_hm":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "short_ymd":
                y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                continue

            # 유효성 검증
            datetime(y, mo, d)
            return f"{y:04d}-{mo:02d}-{d:02d}"

        except (ValueError, IndexError):
            continue

    return None
