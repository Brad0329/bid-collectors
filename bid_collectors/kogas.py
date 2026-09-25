"""한국가스공사 입찰정보 수집기 (F-012, v1.4.0).

API: GET https://apis.data.go.kr/B551210/bidInfoList2/getBidInfoList2 (data.go.kr 15157366)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요
응답: XML(UTF-8). 필드 14개 — NOTICE_CODE(10자리)·NOTICE_NAME·WORK_TYPE_NAME·CONT_METHOD_NAME·NOTICE_DT·END_DT·CANCEL_YN…
날짜: DOCDATE_START/END = 공고일(NOTICE_DT) 범위. **날짜를 빼거나 파라미터 이름이 틀려도 00 + 0건**이 온다(2026-09-25 실측) —
      에러와 0건을 구분할 수 없으니 두 날짜를 늘 보낸다. numOfRows 상한 없음(2000 요청에 1,011건).
알리오엔 가스공사 공고의 약 1/6만 올라온다(9/22~23 API 36건 중 6건) — 이 수집기가 필요한 이유.
취소는 공고일 그대로 CANCEL_YN=취소로만 표시된다(extra 원문).
"""

import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import fetch_pages, parse_xml
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

API_URL = "https://apis.data.go.kr/B551210/bidInfoList2/getBidInfoList2"
ROWS = 1000
DEFAULT_MAX_PAGES = 20
ORGANIZATION = "한국가스공사"  # 단일 기관 API라 응답에 기관 필드가 없다 — 알리오 pname과 같은 이름
# 가스공사 전자입찰 상세 — 알리오 refrUrl 11건 전부 bid_code=001·round=01(2026-09-25). API 응답엔 두 값이 없다.
DETAIL_URL = "https://bid.kogas.or.kr:9443/supplier/contents/bid/bid_detail_view_notice.jsp"


class KogasCollector(BaseCollector):
    """한국가스공사 입찰정보 수집기."""

    source_name = "가스공사"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            "serviceKey": self.api_key,
            "DOCDATE_START": start.strftime("%Y%m%d"),
            "DOCDATE_END": end.strftime("%Y%m%d"),
        }
        async with create_client(timeout=30.0) as client:
            items, pages, errors = await fetch_pages(
                client, API_URL, params, parse_xml,
                rows=ROWS, max_pages=kwargs.get("max_pages", DEFAULT_MAX_PAGES),
                label="[가스공사]", mask=self._mask,
            )

        notices: list[Notice] = []
        skips: Counter[str] = Counter()
        for item in items:
            try:
                notices.append(_item_to_notice(item))
            except Exception as e:
                self._record_skip(skips, e, item)
        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages, errors

    async def health_check(self) -> dict:
        start = time.time()
        try:
            today = datetime.now()
            async with create_client(timeout=15.0) as client:
                resp = await client.get(API_URL, params={
                    "serviceKey": self.api_key, "pageNo": "1", "numOfRows": "1",
                    "DOCDATE_START": (today - timedelta(days=14)).strftime("%Y%m%d"),
                    "DOCDATE_END": today.strftime("%Y%m%d"),
                })
                resp.raise_for_status()
                parse_xml(resp.content)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _item_to_notice(item) -> Notice:
    def t(tag: str) -> str:
        return (item.findtext(tag) or "").strip()

    code = t("NOTICE_CODE")
    title = " ".join(t("NOTICE_NAME").split())
    start_str = parse_date(t("NOTICE_DT"))
    require_fields(NOTICE_CODE=code, NOTICE_NAME=title, NOTICE_DT=start_str)
    end_str = parse_date(t("END_DT"))  # "2026-09-30 10:00"
    url = f"{DETAIL_URL}?notice_code={code}&bid_code=001&round=01"
    return Notice(
        source="가스공사",
        bid_no=f"KOGAS-{code}",
        title=title,
        organization=ORGANIZATION,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
        budget=None,  # 응답에 예산 필드가 없다(낙찰금액 SUCCESS_AMT뿐)
        category=t("WORK_TYPE_NAME"),
        extra=raw_fields(item),
    )
