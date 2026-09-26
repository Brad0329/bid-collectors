"""기업마당 지원사업정보 수집기.

API: https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do
응답: JSON
인증: crtfcKey (BIZINFO_API_KEY)
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.dates import split_period
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import as_text, clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
DEFAULT_PAGE_UNIT = 100


class BizinfoCollector(BaseCollector):
    """기업마당 지원사업정보 수집기."""

    source_name = "기업마당"

    def _env_key(self) -> str:
        return "BIZINFO_API_KEY"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        cutoff = (datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)
        total_cnt = 0
        skips: Counter[str] = Counter()

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
                    msg = self._mask(f"페이지 {page} 요청 실패: {type(e).__name__}: {e}")
                    logger.error(f"[기업마당] {msg}")
                    errors.append(msg)
                    break

                items = data.get("jsonArray", [])
                if not items:
                    break

                pages_processed += 1
                total_cnt = items[0].get("totCnt", 0) if items else 0
                page_has_old = False

                for item in items:
                    try:
                        notice = _item_to_notice(item, cutoff)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
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
            else:
                msg = (
                    f"max_pages={max_pages} 상한 도달로 중단 — API 전체 {total_cnt}건 중 "
                    f"{max_pages * DEFAULT_PAGE_UNIT}건까지만 조회"
                )
                logger.warning(f"[기업마당] {msg}")
                errors.append(msg)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages_processed, errors

    async def health_check(self) -> dict:
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
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)), "response_time_ms": ms}


def _is_within_cutoff(item: dict, cutoff: datetime) -> bool:
    """항목의 생성일이 cutoff 이내인지. 값이 없거나 읽을 수 없으면 True(멈추지 않는다).

    어떤 값(null·숫자·list)에도 예외를 던지지 않는다 — 항목 변환 try 밖(`items[-3:]` 조기 종료 판정)에서 불려,
    여기서 예외가 나면 앞 페이지까지 결과 전체를 잃는다(v1.2.5 B).
    """
    creat = as_text(item.get("creatPnttm")).strip()
    if not creat:
        return True
    try:
        return datetime.strptime(creat[:10], "%Y-%m-%d") >= cutoff
    except ValueError:
        return True


def _item_to_notice(item: dict, cutoff: datetime) -> Notice | None:
    """API 응답 항목을 Notice 모델로 변환. cutoff 이전이면 None."""
    creat_str = as_text(item.get("creatPnttm"))
    if creat_str:
        try:
            creat_dt = datetime.strptime(creat_str[:10], "%Y-%m-%d")
            if creat_dt < cutoff:
                return None
        except ValueError:
            pass  # 형식이 다르면 기준일 판정을 못 한다 — 버리지 않고 통과시킨다(_is_within_cutoff와 같은 규칙)

    pblanc_id = item.get("pblancId")
    title = item.get("pblancNm")
    require_fields(pblancId=pblanc_id, pblancNm=title)
    url = as_text(item.get("pblancUrl"))

    # 신청기간 파싱 — 선택 필드가 null이어도 항목을 버리지 않는다(v1.2.5 B: 종전엔 `"~" in None`으로 TypeError)
    req_period = as_text(item.get("reqstBeginEndDe"))
    # 기간 형식 "2024-03-01 ~ 2024-04-05"만 시작·끝으로 나눈다. 날짜 하나뿐인 값은 시작인지 마감인지 알 수 없어
    # 어느 칸에도 넣지 않는다(v1.6.0 — 종전엔 시작일로 넣었다. 원문은 extra)
    start_str, end_str = split_period(req_period) or (None, None)

    status = determine_status(end_str) if end_str else "ongoing"

    content = clean_html_to_text(as_text(item.get("bsnsSumryCn")))

    return Notice(
        source="기업마당",
        bid_no=f"BIZINFO-{pblanc_id}",
        title=title,
        organization=as_text(item.get("excInsttNm")),
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=url,
        detail_url=url,
        content=content,
        region="",  # 응답에 지역 필드가 없다 — 종전 jrsdInsttNm은 소관기관(부처명)이었다(v1.6.0, extra 원문)
        category=as_text(item.get("pldirSportRealmLclasCodeNm")),
        attachments=_parse_attachments(item),
        extra=raw_fields(item),  # 원문 전부(v1.2.5, 원칙 ①) — 해시태그·대상·담당자 등은 응답 키 그대로
    )


def _parse_attachments(item: dict) -> list[dict] | None:
    """첨부파일 정보 추출."""
    attachments = []
    file_name = as_text(item.get("printFileNm"))
    file_url = as_text(item.get("printFlpthNm"))
    if file_name and file_url:
        attachments.append({"name": file_name, "url": file_url})

    file_name2 = as_text(item.get("fileNm"))
    file_url2 = as_text(item.get("flpthNm"))
    if file_name2 and file_url2:
        attachments.append({"name": file_name2, "url": file_url2})

    return attachments or None
