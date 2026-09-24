"""K-Startup (창업진흥원) 사업공고 수집기.

API: https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01
인증: serviceKey (DATA_GO_KR_KEY)
응답: JSON (odcloud 형식 — data 배열, totalCount)
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, require_fields
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

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        only_ongoing = kwargs.get("only_ongoing", True)
        cutoff = (datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        max_pages = kwargs.get("max_pages", 50)
        total_count = 0
        skips: Counter[str] = Counter()

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
                    msg = self._mask(f"페이지 {page} 요청 실패: {type(e).__name__}: {e}")
                    logger.error(f"[K-Startup] {msg}")
                    errors.append(msg)
                    break

                items = data.get("data", [])
                if not items:
                    break

                pages_processed += 1
                total_count = data.get("totalCount", 0)

                for item in items:
                    try:
                        notice = _item_to_notice(item, cutoff)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
                    if notice is not None:
                        notices.append(notice)

                if page * DEFAULT_PER_PAGE >= total_count:
                    break
                page += 1
            else:
                msg = (
                    f"max_pages={max_pages} 상한 도달로 중단 — 전체 {total_count}건 중 "
                    f"{max_pages * DEFAULT_PER_PAGE}건까지만 조회"
                )
                logger.warning(f"[K-Startup] {msg}")
                errors.append(msg)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)

        return notices, pages_processed, errors

    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회. content 전문 + 추가 필드.

        Args:
            bid_no: "KSTARTUP-{pbanc_sn}" 형식

        Returns:
            상세 정보 dict 또는 None
        """
        pbanc_id = bid_no.replace("KSTARTUP-", "")
        if not pbanc_id:
            return None

        try:
            async with create_client(timeout=15.0) as client:
                params = {
                    "serviceKey": self.api_key,
                    "page": "1",
                    "perPage": "1",
                    "returnType": "json",
                    "cond[pbanc_sn::EQ]": pbanc_id,
                }
                resp = await client.get(API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

                items = data.get("data", [])
                if not items:
                    return None

                item = items[0]
                content = clean_html_to_text(item.get("pbanc_ctnt", "") or "")

                return {
                    k: v for k, v in {
                        "content": content,
                        "target": clean_html_to_text(item.get("aply_trgt_ctnt") or ""),
                        "target_age": item.get("biz_trgt_age") or "",
                        "biz_year": item.get("biz_enyy") or "",
                        "excl_target": clean_html_to_text(item.get("aply_excl_trgt_ctnt") or ""),
                        "apply_method": clean_html_to_text(
                            item.get("aply_mthd_onli_rcpt_istc")
                            or item.get("aply_mthd_vst_rcpt_istc")
                            or item.get("aply_mthd_etc_istc")
                            or ""
                        ),
                        "department": clean_html_to_text(item.get("biz_prch_dprt_nm") or ""),
                        "contact": item.get("prch_cnpl_no") or "",
                        "biz_name": clean_html_to_text(item.get("intg_pbanc_biz_nm") or ""),
                        "apply_url": item.get("biz_aply_url") or "",
                    }.items() if v
                } or None

        except Exception as e:
            logger.warning(f"[K-Startup] fetch_detail 실패 ({bid_no}): {self._mask(str(e))}")
            return None

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
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)), "response_time_ms": ms}


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

    # str() 전에 검사한다 — str(None)은 "None"이라 빈 ID가 "KSTARTUP-None"으로 통과한다
    pblanc_sn = item.get("pbanc_sn")
    title = clean_html(item.get("biz_pbanc_nm") or "")
    require_fields(pbanc_sn=pblanc_sn, biz_pbanc_nm=title)
    pblanc_sn = str(pblanc_sn)
    content = clean_html_to_text(item.get("pbanc_ctnt", "") or "")

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
