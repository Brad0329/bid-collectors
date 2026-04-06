"""기업마당 지원사업정보 수집기.

API: https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do
응답: JSON
인증: crtfcKey (BIZINFO_API_KEY)
"""

import logging
from datetime import datetime, timedelta

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
DEFAULT_PAGE_UNIT = 100


class BizinfoCollector(BaseCollector):
    """기업마당 지원사업정보 수집기."""

    source_name = "기업마당"

    def _env_key(self) -> str:
        return "BIZINFO_API_KEY"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        cutoff = datetime.now() - timedelta(days=days)
        notices: list[Notice] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)

        async with create_client(timeout=30.0) as client:
            page = 1
            while page <= max_pages:
                params = {
                    "crtfcKey": self.api_key,
                    "dataType": "json",
                    "pageUnit": str(DEFAULT_PAGE_UNIT),
                    "pageIndex": str(page),
                }

                try:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.error(f"[기업마당] 페이지 {page} 요청 실패: {e}")
                    break

                items = data.get("jsonArray", [])
                if not items:
                    break

                pages_processed += 1
                total_cnt = items[0].get("totCnt", 0) if items else 0
                page_has_old = False

                for item in items:
                    notice = _item_to_notice(item, cutoff)
                    if notice is None:
                        page_has_old = True
                        continue
                    notices.append(notice)

                # 다음 페이지 확인
                if page * DEFAULT_PAGE_UNIT >= total_cnt:
                    break
                if page_has_old and not any(
                    _is_within_cutoff(it, cutoff) for it in items[-3:]
                ):
                    break
                page += 1

        return notices, pages_processed

    async def health_check(self) -> dict:
        import time
        start = time.time()
        try:
            async with create_client(timeout=10.0) as client:
                params = {
                    "crtfcKey": self.api_key,
                    "dataType": "json",
                    "pageUnit": "1",
                    "pageIndex": "1",
                }
                resp = await client.get(API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("jsonArray"):
                    raise ValueError("빈 응답")
                ms = int((time.time() - start) * 1000)
                return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}


def _is_within_cutoff(item: dict, cutoff: datetime) -> bool:
    """항목의 생성일이 cutoff 이내인지 확인."""
    creat = item.get("creatPnttm", "")
    if not creat:
        return True
    try:
        dt = datetime.strptime(creat[:10], "%Y-%m-%d")
        return dt >= cutoff
    except (ValueError, IndexError):
        return True


def _item_to_notice(item: dict, cutoff: datetime) -> Notice | None:
    """API 응답 항목을 Notice 모델로 변환. cutoff 이전이면 None."""
    creat_str = item.get("creatPnttm", "")
    if creat_str:
        try:
            creat_dt = datetime.strptime(creat_str[:10], "%Y-%m-%d")
            if creat_dt < cutoff:
                return None
        except (ValueError, IndexError):
            pass

    pblanc_id = item.get("pblancId", "")
    title = item.get("pblancNm", "")
    url = item.get("pblancUrl", "")

    # 신청기간 파싱
    req_period = item.get("reqstBeginEndDe", "")
    start_str = parse_date(req_period)
    # reqstBeginEndDe에서 종료일 추출 시도 (기간 형식: "2024-03-01 ~ 2024-04-05")
    end_str = None
    if "~" in req_period:
        parts = req_period.split("~")
        if len(parts) == 2:
            end_str = parse_date(parts[1].strip())

    status = determine_status(end_str) if end_str else "ongoing"

    content = clean_html_to_text(item.get("bsnsSumryCn", ""))

    return Notice(
        source="기업마당",
        bid_no=f"BIZINFO-{pblanc_id}",
        title=title,
        organization=item.get("excInsttNm", ""),
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=url,
        detail_url=url,
        content=content,
        region=item.get("jrsdInsttNm", ""),
        category=item.get("pldirSportRealmLclasCodeNm", ""),
        attachments=_parse_attachments(item),
        extra={
            k: v for k, v in {
                "sub_category": item.get("pldirSportRealmMlsfcCodeNm", ""),
                "target": item.get("trgetNm", ""),
                "hashtags": item.get("hashtags", ""),
                "reference": item.get("refrncNm", ""),
                "req_method": item.get("reqstMthPapersCn", ""),
                "view_count": item.get("inqireCo"),
            }.items() if v
        } or None,
    )


def _parse_attachments(item: dict) -> list[dict] | None:
    """첨부파일 정보 추출."""
    attachments = []
    file_name = item.get("printFileNm", "")
    file_url = item.get("printFlpthNm", "")
    if file_name and file_url:
        attachments.append({"name": file_name, "url": file_url})

    file_name2 = item.get("fileNm", "")
    file_url2 = item.get("flpthNm", "")
    if file_name2 and file_url2:
        attachments.append({"name": file_name2, "url": file_url2})

    return attachments or None
