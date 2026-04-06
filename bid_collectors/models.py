"""수집기 공통 데이터 모델."""

from datetime import date, datetime
from pydantic import BaseModel


class Notice(BaseModel):
    """수집기가 반환하는 공고 1건의 표준 모델."""

    # 필수
    source: str
    bid_no: str
    title: str
    organization: str

    # 날짜/상태
    start_date: date | None = None
    end_date: date | None = None
    status: str = "ongoing"

    # URL
    url: str
    detail_url: str = ""

    # 내용
    content: str = ""
    budget: int | None = None
    region: str = ""
    category: str = ""

    # 첨부파일: [{"name": "파일명.pdf", "url": "https://..."}, ...]
    attachments: list[dict] | None = None

    # 수집기별 추가 데이터 (BidWatch에서 JSONB로 저장)
    extra: dict | None = None


class CollectResult(BaseModel):
    """collect() 메서드의 반환 타입."""

    # 수집 데이터
    notices: list[Notice]

    # 메타데이터
    source: str
    collected_at: datetime
    duration_seconds: float
    total_fetched: int
    total_after_dedup: int
    pages_processed: int

    # 에러
    errors: list[str] = []
    is_partial: bool = False
