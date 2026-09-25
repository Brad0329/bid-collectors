"""LH(한국토지주택공사) 입찰공고 수집기 (F-011, v1.4.0).

API: GET https://apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo (data.go.kr 15159012)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요
응답: XML, **EUC-KR 선언** — 바이트 그대로 파싱한다(utils/datagokr.py)
날짜: tndrbidRegDtStart/End(YYYYMMDD) = 공고일(tndrbidRegDt) 범위. 빈 결과는 resultCode 03 NODATA.
실측(2026-09-25, docs/institution_sources.md): numOfRows 상한 없음(5000 요청에 3,791건), 14일 139건·하루 최대 38건.
정정·취소 공고는 새 행이 아니라 **같은 행의 bidDegree(00→01)와 공고일이 정정일로 바뀐다** — bid_no에 차수를 넣지 않는다.
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

API_URL = "https://apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo"
ROWS = 1000
DEFAULT_MAX_PAGES = 20
NODATA = ("03",)
ORGANIZATION = "한국토지주택공사"  # 단일 기관 API라 응답에 기관 필드가 없다 — 알리오 pname과 같은 이름(지역본부는 extra의 zoneHqCd)

# LH 전자입찰 상세 화면 — 업무 구분(cstrtnJobGbNm)마다 경로가 다르다. 알리오 refrUrl 17건에서 확인한 3종만 둔다(2026-09-25).
_DETAIL_BASE = "https://ebid.lh.or.kr/ebid.et.tp.cmd."
_DETAIL_CMD = {
    "시설공사": "BidConstructDetailListCmd",
    "용역": "BidsrvcsDetailListCmd",
    "지급자재": "BidctrctgdsDetailListCmd",
}
# 물품(365일 76건)은 상세 경로 표본이 없다 — 추측한 경로로 깨진 링크를 주지 않고 전자입찰 첫 화면으로
LIST_URL = "https://ebid.lh.or.kr/"


class LhCollector(BaseCollector):
    """LH 입찰공고 수집기."""

    source_name = "LH"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            "serviceKey": self.api_key,
            "tndrbidRegDtStart": start.strftime("%Y%m%d"),
            "tndrbidRegDtEnd": end.strftime("%Y%m%d"),
        }
        async with create_client(timeout=30.0) as client:
            items, pages, errors = await fetch_pages(
                client, API_URL, params, lambda c: parse_xml(c, NODATA),
                rows=ROWS, max_pages=kwargs.get("max_pages", DEFAULT_MAX_PAGES),
                label="[LH]", mask=self._mask,
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
                    "tndrbidRegDtStart": (today - timedelta(days=7)).strftime("%Y%m%d"),
                    "tndrbidRegDtEnd": today.strftime("%Y%m%d"),
                })
                resp.raise_for_status()
                parse_xml(resp.content, NODATA)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def detail_url(job_type: str, bid_num: str, degree: str) -> str:
    cmd = _DETAIL_CMD.get(job_type)
    if not cmd:
        return LIST_URL
    return f"{_DETAIL_BASE}{cmd}.dev?bidNum={bid_num}&bidDegree={degree or '00'}"


def _item_to_notice(item) -> Notice:
    def t(tag: str) -> str:
        return (item.findtext(tag) or "").strip()

    bid_num = t("bidNum")
    title = " ".join(t("bidnmKor").split())
    start_str = parse_date(t("tndrbidRegDt"))
    require_fields(bidNum=bid_num, bidnmKor=title, tndrbidRegDt=start_str)
    end_str = parse_date(t("tndrdocAcptEndDtm"))  # 입찰서 접수 마감 "2026/09/21 10:00"
    job_type = t("cstrtnJobGbNm")
    url = detail_url(job_type, bid_num, t("bidDegree"))
    return Notice(
        source="LH",
        bid_no=f"LH-{bid_num}",
        title=title,
        organization=ORGANIZATION,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
        budget=None,  # 추정가격·설계가·기초금액 중 무엇을 budget으로 볼지는 원칙 ② 결정 — 금액은 extra 원문(2026-09-26 사용자)
        category=job_type,
        extra=raw_fields(item),
    )
