"""알리오(공공기관 경영정보 공개시스템) 입찰공고 수집기.

API: GET https://alio.go.kr/occasional/findBidList.json?type=title&word=&pageNo=N&area=
     (알리오 입찰공고 화면 bidList.do가 부르는 공개 JSON — 인증 없음, 페이지당 10건)
응답: {"status": "success", "data": {"result": [...], "totalCnt": N, "page": {...}}}
      항목: rtitle(제목) pname(기관) bdate(공고일 YYYY.MM.DD) bidInfoEndDt(마감일) seq(상세 번호)
정렬: 공고일 최신순 — 기준일보다 오래된 공고가 나오면 멈춘다(서버 날짜 필터 없음).

왜 필요한가: 자체 전자조달을 쓰는 공기업(수자원·코레일·한전·LH…)은 나라장터 API에 공고가 거의 없고
알리오에 모인다(bidwatch docs/procurement_sources_research.md 3-1, 2026-09-24 실측).
"""

import logging
import time
from datetime import datetime, timedelta

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

logger = logging.getLogger("bid_collectors")

API_URL = "https://alio.go.kr/occasional/findBidList.json"
DETAIL_URL = "https://alio.go.kr/occasional/bidDtl.do"
DEFAULT_MAX_PAGES = 50  # 10건/페이지 — 하루 약 50건(2026-09-24 실측)이라 10일치 안팎


class AlioCollector(BaseCollector):
    """알리오 공공기관 입찰공고 수집기. API 키가 필요 없다."""

    source_name = "알리오"

    def __init__(self, api_key: str | None = None, **kwargs):
        # 공개 JSON이라 키가 없다 — BaseCollector의 키 필수 검사를 건너뛴다(GenericScraper와 같은 자리).
        # 소비자가 다른 수집기와 같은 모양으로 api_key를 넘겨도 받아서 쓰지 않는다.
        self.api_key = None

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        cutoff = (datetime.now() - timedelta(days=days)).date()  # date라 자정 절삭과 같다
        max_pages = kwargs.get("max_pages", DEFAULT_MAX_PAGES)
        notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        skipped = 0
        total_count = 0
        reached_cutoff = False

        async with create_client(timeout=20.0) as client:
            page = 1
            while page <= max_pages:
                try:
                    resp = await client.get(API_URL, params={
                        "type": "title", "word": "", "pageNo": str(page), "area": "",
                    })
                    resp.raise_for_status()
                    items, total_count = _parse_response(resp.json())
                except Exception as e:
                    msg = f"페이지 {page} 요청 실패: {type(e).__name__}: {e}"
                    logger.error(f"[알리오] {msg}")
                    errors.append(msg)
                    break

                if not items:
                    break
                pages_processed += 1

                for item in items:
                    try:
                        notice = _item_to_notice(item)
                    except Exception as e:
                        logger.warning(f"[알리오] 항목 파싱 실패: {e}", exc_info=True)
                        skipped += 1
                        continue
                    if notice.start_date and notice.start_date < cutoff:
                        reached_cutoff = True
                        break
                    notices.append(notice)

                if reached_cutoff:
                    break
                page += 1
            else:
                msg = (f"max_pages={max_pages} 상한 도달로 중단 — 전체 {total_count}건 중 "
                       f"최근 {max_pages * 10}건까지만 조회, 기준일({cutoff})까지 못 닿음")
                logger.warning(f"[알리오] {msg}")
                errors.append(msg)

        if skipped:
            msg = f"항목 파싱 예외로 {skipped}건 건너뜀"
            logger.warning(f"[알리오] {msg}")
            errors.append(msg)
        return notices, pages_processed, errors

    async def health_check(self) -> dict:
        start = time.time()
        try:
            async with create_client(timeout=10.0) as client:
                resp = await client.get(API_URL, params={"type": "title", "word": "", "pageNo": "1", "area": ""})
                resp.raise_for_status()
                _parse_response(resp.json())
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": str(e),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _parse_response(body: dict) -> tuple[list[dict], int]:
    """응답 JSON → (항목 목록, 전체 건수). status가 success가 아니면 ValueError."""
    if body.get("status") != "success":
        raise ValueError(f"API 응답 status={body.get('status')!r} message={body.get('message')!r}")
    data = body.get("data") or {}
    return data.get("result") or [], int(data.get("totalCnt") or 0)


def _item_to_notice(item: dict) -> Notice:
    seq = item["seq"]  # 없으면 식별이 안 된다 — 예외로 건너뛰고 건수를 보고한다
    start_str = parse_date(item.get("bdate") or "")
    end_str = parse_date(item.get("bidInfoEndDt") or "")
    url = f"{DETAIL_URL}?seq={seq}"
    return Notice(
        source="알리오",
        bid_no=f"ALIO-{seq}",
        title=" ".join((item.get("rtitle") or "").split()),
        organization=(item.get("pname") or "").strip(),
        start_date=start_str or None,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
    )
