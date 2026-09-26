"""K-Startup (창업진흥원) 사업공고 수집기.

API: https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01
인증: serviceKey (DATA_GO_KR_KEY)
응답: JSON (odcloud 형식 — data 배열, matchCount = 필터 적용 후 건수, totalCount = 필터 무관 전체)
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status
from .utils.text import as_text, clean_html, clean_html_to_text

logger = logging.getLogger("bid_collectors")

API_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
DEFAULT_PER_PAGE = 100


class KstartupCollector(BaseCollector):
    """K-Startup 사업공고 수집기."""

    source_name = "K-Startup"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        # 기본 True = 원칙 ②의 명시적 예외(2026-09-26 사용자): 등록일 필드가 없고, 서버 날짜 필터(cond[...::GTE])는
        # matchCount에만 먹고 데이터엔 안 먹는다(100건 중 98건 위반) — 진행중이 범위를 좁히는 유일한 조건(필터 없으면 30,168건)
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

                if "code" in data and data["code"] < 0:
                    msg = self._mask(f"페이지 {page} API 에러: {data['code']} - {data.get('msg', '')}")
                    logger.error(f"[K-Startup] {msg}")
                    errors.append(msg)
                    break

                items = data.get("data", [])
                if not items:
                    break

                pages_processed += 1
                # 필터(cond) 적용 후 건수 — totalCount는 필터와 무관한 전체 건수(실측 30168 vs 진행중 230)라
                # 종료 판정이 늦어 빈 페이지를 1회 더 불렀다(v1.3.1, 보조금24와 같은 방식)
                total_count = data.get("matchCount", 0)

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
    # 선택 필드는 null·타입 이상이어도 항목을 버리지 않는다(v1.2.5 B) — 원문은 extra에 그대로 남는다
    start_raw = as_text(item.get("pbanc_rcpt_bgng_dt"))
    end_raw = as_text(item.get("pbanc_rcpt_end_dt"))
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

    # 상태: 출처의 모집 진행 여부(rcrt_prgs_yn Y/N) 우선, 값이 없을 때만 마감일 판정(원칙 ②의 명시적 예외).
    # 종전엔 값이 없으면 "closed" 상수였다(v1.6.0)
    prgs = item.get("rcrt_prgs_yn")
    status = {"Y": "ongoing", "N": "closed"}.get(prgs) or determine_status(end_str)

    # str() 전에 검사한다 — str(None)은 "None"이라 빈 ID가 "KSTARTUP-None"으로 통과한다
    pblanc_sn = item.get("pbanc_sn")
    title = item.get("biz_pbanc_nm")
    if isinstance(title, str):
        title = clean_html(title)
    require_fields(pbanc_sn=pblanc_sn, biz_pbanc_nm=title)  # 문자열이 아닌 제목은 아래 Notice에서 ValidationError → 형식 이상으로 건너뛴다
    pblanc_sn = str(pblanc_sn)
    content = clean_html_to_text(as_text(item.get("pbanc_ctnt")))

    detail_url = as_text(item.get("detl_pg_url"))
    apply_url = as_text(item.get("biz_aply_url"))
    url = detail_url or apply_url or as_text(item.get("biz_gdnc_url"))

    return Notice(
        source="K-Startup",
        bid_no=f"KSTARTUP-{pblanc_sn}",
        title=title,
        # 공고기관 원문 한 필드(v1.6.0) — 종전 폴백 sprv_inst는 기관 유형("민간" 등), "창업진흥원"은 상수였다
        organization=as_text(item.get("pbanc_ntrp_nm")),
        start_date=start_str or None,
        end_date=end_str or None,
        status=status,
        url=url,
        detail_url=detail_url,
        content=content,  # 절단 없음(v1.6.0 — 종전 500자)
        region=as_text(item.get("supt_regin")),
        category=as_text(item.get("supt_biz_clsfc")),
        # 원문 전부(v1.2.5, 원칙 ①) — 대상·신청방법·담당부서 등은 응답 키 그대로, HTML도 원문 그대로(표시용 정리는 소비자 몫)
        extra=raw_fields(item),
    )
