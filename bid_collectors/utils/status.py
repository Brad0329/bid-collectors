"""공고 상태 판정 유틸리티.

ongoing/closed의 마감일 판정은 원칙 ②(추정 금지)의 **명시적 예외**다 — 편의 계산값(2026-09-26 사용자, CONTRACT.md).
`cancelled`는 출처가 명시한 취소 표시만으로 정한다(v1.6.0 — bidwatch F-017과 같은 키·값).
"""

from datetime import date, datetime

# 출처가 주는 취소 표시 값 — 나라장터 ntceKindNm · LH bidKind · d2b 경쟁 pblancSe = "취소공고",
# 가스공사 CANCEL_YN = "취소", d2b 수의 progrsSttus = "공개협상취소" (각 수집기가 자기 키를 읽는다)
CANCEL_KIND = "취소공고"


def determine_status(end_date_str: str | None, date_format: str = "%Y-%m-%d", *, cancelled: bool = False) -> str:
    """상태 판정.

    Args:
        end_date_str: 마감일 문자열 ('yyyy-MM-dd')
        date_format: 날짜 형식
        cancelled: 출처가 취소라고 명시했는가 — 참이면 마감일과 무관하게 'cancelled'

    Returns:
        'cancelled', 'ongoing' 또는 'closed'
    """
    if cancelled:
        return "cancelled"
    if not end_date_str:
        return "ongoing"

    try:
        end = datetime.strptime(end_date_str, date_format).date()
        return "ongoing" if end >= date.today() else "closed"
    except (ValueError, TypeError):
        return "ongoing"
