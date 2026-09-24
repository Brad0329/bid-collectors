"""알리오(공공기관 경영정보 공개시스템) 입찰공고 수집기.

API: GET https://alio.go.kr/occasional/findBidList.json?type=title&word=&pageNo=N&area=
     (알리오 입찰공고 화면 bidList.do가 부르는 공개 JSON — 인증 없음, 페이지당 10건)
응답: {"status": "success", "data": {"result": [...], "totalCnt": N, "page": {...}}}
      항목: rtitle(제목) pname(기관) bdate(공고일 YYYY.MM.DD) bidInfoEndDt(마감일) seq(상세 번호)
정렬: 등록(seq) 최신순 — 공고일은 대체로 내려가지만 뒤섞인다. 서버 날짜 필터가 없어, 기준일 이전 항목은 버리고
      기준일 이후 항목이 없는 페이지가 OLD_PAGES_TO_STOP번 연달아 나오면 멈춘다(2026-09-24 누락 결함 수정 — v1.2.3).

왜 필요한가: 자체 전자조달을 쓰는 공기업(수자원·코레일·한전·LH…)은 나라장터 API에 공고가 거의 없고
알리오에 모인다(bidwatch docs/procurement_sources_research.md 3-1, 2026-09-24 실측).
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, require_fields
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

logger = logging.getLogger("bid_collectors")

API_URL = "https://alio.go.kr/occasional/findBidList.json"
DETAIL_URL = "https://alio.go.kr/occasional/bidDtl.do"
# 10건/페이지, 평일 하루 약 450~500건(2026-09-24 실측: 9/22 506·9/23 439), 페이지당 약 2.5초.
# days=1은 오늘+어제라 약 100페이지 → 150페이지 ≈ 3일치. 더 긴 기간은 상한에서 멈추고 errors로 알린다.
# (종전 주석 "하루 약 160건·50페이지 ≈ 3일치"는 아래 조기 종료 결함으로 덜 받은 474건으로 계산한 틀린 값이었다.)
DEFAULT_MAX_PAGES = 150
# 목록은 공고일 순이 아니라 등록(seq) 순이다 — 오래된 공고일 항목이 새 항목 사이에 끼어 온다(110페이지 실측: 역전 3곳).
# 그래서 오래된 항목 하나에서 멈추지 않고, 기준일 이후 항목이 하나도 없는 페이지가 이만큼 연달아 나와야 멈춘다.
# 1이 아니라 2인 이유: 한 기관이 과거 공고일 공고를 한꺼번에 등록하면 한 페이지가 통째로 오래될 수 있다.
OLD_PAGES_TO_STOP = 2


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
        skips: Counter[str] = Counter()
        total_count = 0
        old_pages = 0  # 기준일 이후 항목이 없는 페이지가 연달아 몇 개인가

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
                dated = recent = 0

                for item in items:
                    try:
                        notice = _item_to_notice(item)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
                    dated += 1
                    if notice.start_date < cutoff:
                        continue  # 오래된 항목은 버리되 멈추지 않는다 — 뒤에 기준일 이후 항목이 더 올 수 있다
                    recent += 1
                    notices.append(notice)

                # 날짜를 읽은 항목이 있는데 전부 기준일 이전인 페이지만 "오래된 페이지"로 센다
                # (전부 건너뛴 페이지는 판단 근거가 없어 세지 않는다)
                old_pages = old_pages + 1 if dated and not recent else 0
                if old_pages >= OLD_PAGES_TO_STOP:
                    break
                page += 1
            else:
                msg = (f"max_pages={max_pages} 상한 도달로 중단 — 전체 {total_count}건 중 "
                       f"최근 {max_pages * 10}건까지만 조회, 기준일({cutoff})까지 못 닿음")
                logger.warning(f"[알리오] {msg}")
                errors.append(msg)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
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
    # 필수: seq(식별)·rtitle·pname·bdate — 2026-09-24 수집 474건에서 빈 값 0건이었다. 마감일은 10건 비어 있어 선택.
    # 공식 API가 없어(procurement_sources_research.md 3-1) 형식 변경을 이렇게라도 감지한다 — 건너뛰고 사유를 errors로.
    seq = item.get("seq")
    title = " ".join((item.get("rtitle") or "").split())
    organization = (item.get("pname") or "").strip()
    start_str = parse_date(item.get("bdate") or "")
    require_fields(seq=seq, rtitle=title, pname=organization, bdate=start_str)
    end_str = parse_date(item.get("bidInfoEndDt") or "")
    url = f"{DETAIL_URL}?seq={seq}"
    return Notice(
        source="알리오",
        bid_no=f"ALIO-{seq}",
        title=title,
        organization=organization,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
    )
