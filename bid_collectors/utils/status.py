"""공고 상태 판정 유틸리티."""

from datetime import date, datetime


def determine_status(end_date_str: str | None, date_format: str = "%Y-%m-%d") -> str:
    """마감일 기준 상태 판정.

    Args:
        end_date_str: 마감일 문자열 ('yyyy-MM-dd')
        date_format: 날짜 형식

    Returns:
        'ongoing' 또는 'closed'
    """
    if not end_date_str:
        return "ongoing"

    try:
        end = datetime.strptime(end_date_str, date_format).date()
        return "ongoing" if end >= date.today() else "closed"
    except (ValueError, TypeError):
        return "ongoing"
