"""K-Startup (창업진흥원) 사업공고 수집기.

API: https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01
인증: serviceKey (DATA_GO_KR_KEY)
응답: JSON (odcloud 형식 — data 배열, totalCount)
"""

import logging
import time
from datetime import datetime, timedelta

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.text import clean_html, clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
DEFAULT_PER_PAGE = 100


class KstartupCollector(BaseCollector):
    """K-Startup 사업공고 수집기."""

    source_name = "K-Startup"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int]:
        only_ongoing = kwargs.get("only_ongoing", True)
        cutoff = (datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        notices: list[Notice] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)

        async with create_client(timeout=30.0) as client:
            page = 1
            while page <= max_pages:
                params = {
                    "serviceKey": self.api_key,
                    "page": str(page),
                    "perPage": str(DEFAULT_PER_PAGE),
                    "returnType": "json",
                }
                if only_ongoing:
                    params["cond[rcrt_prgs_yn::EQ]"] = "Y"

                try:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.error(f"[K-Startup] 페이지 {page} 요청 실패: {e}")
                    break

                items = data.get("data", [])
                if not items:
                    break

                pages_processed += 1
                total_count = data.get("totalCount", 0)

                for item in items:
                    notice = _item_to_notice(item, cutoff)
                    if notice is not None:
                        notices.append(notice)

                if page * DEFAULT_PER_PAGE >= total_count:
                    break
                page += 1

        return notices, pages_processed

    async def health_check(self) -> dict:
        start = time.time()
        try:
            async with create_client(timeout=10.0) as client:
                params = {
                    "serviceKey": self.api_key,
                    "page": "1",
                    "perPage": "1",
                    "returnType": "json",
                }
                resp = await client.get(API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("data"):
                    raise ValueError("빈 응답")
                ms = int((time.time() - start) * 1000)
                return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"status": "error", "source": self.source_name, "message": str(e), "response_time_ms": ms}


def _item_to_notice(item: dict, cutoff: datetime) -> Notice | None:
    """API 응답 항목을 Notice 모델로 변환. cutoff 이전이면 None."""
    # 날짜 파싱
    start_raw = item.get("pbanc_rcpt_bgng_dt", "") or ""
    end_raw = item.get("pbanc_rcpt_end_dt", "") or ""
    start_str = parse_date(start_raw)
    end_str = parse_date(end_raw)

    # cutoff 필터링 (공고 접수 시작일 기준)
    if start_str:
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            if start_dt < cutoff:
                return None
        except ValueError:
            pass

    # 상태: API 필드 우선, 없으면 날짜 기반 판정
    status = "ongoing" if item.get("rcrt_prgs_yn") == "Y" else "closed"

    title = clean_html(item.get("biz_pbanc_nm", ""))
    content = clean_html_to_text(item.get("pbanc_ctnt", "") or "")
    pblanc_sn = str(item.get("pbanc_sn", ""))

    detail_url = item.get("detl_pg_url") or ""
    apply_url = item.get("biz_aply_url") or ""
    url = detail_url or apply_url or item.get("biz_gdnc_url") or ""

    return Notice(
        source="K-Startup",
        bid_no=f"KSTARTUP-{pblanc_sn}",
        title=title,
        organization=item.get("pbanc_ntrp_nm") or item.get("sprv_inst") or "창업진흥원",
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=url,
        detail_url=detail_url,
        content=content[:500] if content else "",
        region=item.get("supt_regin") or "",
        category=item.get("supt_biz_clsfc") or "",
        extra={
            k: v for k, v in {
                "target": clean_html(item.get("aply_trgt_ctnt") or ""),
                "apply_url": apply_url,
                "contact": item.get("prch_cnpl_no") or "",
                "apply_method": clean_html(
                    item.get("aply_mthd_onli_rcpt_istc")
                    or item.get("aply_mthd_vst_rcpt_istc")
                    or item.get("aply_mthd_etc_istc")
                    or ""
                ),
                "biz_year": item.get("biz_enyy") or "",
                "target_age": item.get("biz_trgt_age") or "",
                "department": clean_html(item.get("biz_prch_dprt_nm") or ""),
                "excl_target": clean_html(item.get("aply_excl_trgt_ctnt") or ""),
                "biz_name": clean_html(item.get("intg_pbanc_biz_nm") or ""),
            }.items() if v is not None and v != ""
        } or None,
    )
