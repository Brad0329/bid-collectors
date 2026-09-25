"""국방전자조달(d2b, 방위사업청) 입찰공고 수집기 (F-013, v1.4.0).

API: https://apis.data.go.kr/1690000/BidPblancInfoService/{목록 5종} (data.go.kr 15158416)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요. **오퍼레이션당 100회/일**(응답 헤더 x-ratelimit 실측)
응답: XML — JSON은 dcsNo가 int/str로 섞여 온다(2026-09-25 실측 136/6, 앞자리 0 유실 우려).
알리오에 없는 공고다(평일 목록 5종 합 하루 약 130건).

목록 5종과 날짜 기준(실측 — docs/institution_sources.md):
- 국내경쟁·시설경쟁: 공고일 범위(anmtDateBegin/End). 목록은 개찰 전 공고만 들고 있다.
- 국외경쟁: 개찰일(opengDateBegin/End)이 필수(빼도 에러 없이 anmt 조건만 먹는다) → 공고일 범위 + 개찰일 기준일~1년 뒤.
  목록에 공고일·발주기관 필드가 없다 → start_date None, organization "방위사업청"(국외 조달은 방위사업청이 직접 한다).
- 국내·시설 공개수의협상: 공고일 필터도 공고일 필드도 없다 → 견적서 제출마감 오늘~1년 뒤(진행 중 전량), start_date None.
날짜 형식이 틀려도 00 + 0건이 온다 — 형식은 코드로 보장한다(YYYYMMDD).
"""

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import fetch_pages, parse_xml
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

BASE_URL = "https://apis.data.go.kr/1690000/BidPblancInfoService/"
ROWS = 1000
DEFAULT_MAX_PAGES = 5
# 상세 링크 필드가 12개 오퍼레이션 어디에도 없다(스키마·응답 확인) — d2b 첫 화면으로
SITE_URL = "https://www.d2b.go.kr/"
FOREIGN_ORGANIZATION = "방위사업청"
OPEN_WINDOW_DAYS = 365  # 개찰일·견적서 마감 "앞으로" 범위


@dataclass(frozen=True)
class ListSpec:
    kind: str                     # bid_no 구분
    operation: str
    title: str
    start: str | None             # 공고일(또는 협상계획일) 필드
    end: str                      # 입찰서·견적서 제출 마감 필드
    key: tuple[str, ...]          # bid_no 키 — 필드 값을 이어 붙인다
    order: str                    # 차수 필드
    dates: str                    # 요청 날짜 조건: anmt | anmt+openg | prqudo


# 차수는 전부 pblancOdr — g2bPblancOdr는 취소·정정 공고가 나와도 그대로라(2026-09-26 실측: 원공고 pblancOdr 1·취소공고 2가
# 같은 g2bPblancOdr 01) 차수로 쓰면 취소공고가 원공고에 합쳐져 사라진다(7일 418건 중 9건).
LISTS = (
    ListSpec("국내경쟁", "getDmstcCmpetBidPblancList", "bidNm", "pblancDate", "biddocPresentnClosDt",
             ("g2bPblancNo",), "pblancOdr", "anmt"),
    ListSpec("국외경쟁", "getOutnatnCmpetBidPblancList", "bsnsNm", None, "bidRegistClosDt",
             ("g2bPblancNo",), "pblancOdr", "anmt+openg"),
    ListSpec("시설경쟁", "getFcltyCmpetBidPblancList", "cntrwkNm", "pblancDate", "biddocPresentnClosDt",
             ("g2bPblancNo",), "pblancOdr", "anmt"),
    # 수의 2종엔 g2bPblancNo가 없다 — 경쟁 목록의 g2bPblancNo와 같은 구성(연도+공고번호+판단/공사번호)을 원문 필드로 만든다.
    # 공고일 필드도 없다 — ntatPlanDate는 앞으로의 협상 예정일(실측 9/28~2027-05-06)이라 start_date로 쓰지 않는다(extra 원문)
    ListSpec("국내수의", "getDmstcOthbcVltrnNtatPlanList", "othbcNtatNm", None, "prqudoPresentnClosDt",
             ("demandYear", "pblancNo", "dcsNo"), "pblancOdr", "prqudo"),
    ListSpec("시설수의", "getFcltyOthbcVltrnNtatPlanList", "cntrwkNm", None, "prqudoPresentnClosDt",
             ("pblancNo", "cntrwkNo"), "pblancOdr", "prqudo"),
)


def _date_params(spec: ListSpec, days: int, now: datetime) -> dict:
    fmt = "%Y%m%d"
    today = now.strftime(fmt)
    begin = (now - timedelta(days=days)).strftime(fmt)
    ahead = (now + timedelta(days=OPEN_WINDOW_DAYS)).strftime(fmt)
    if spec.dates == "prqudo":
        return {"prqudoPresentnClosDateBegin": today, "prqudoPresentnClosDateEnd": ahead}
    params = {"anmtDateBegin": begin, "anmtDateEnd": today}
    if spec.dates == "anmt+openg":
        params |= {"opengDateBegin": begin, "opengDateEnd": ahead}
    return params


class D2bCollector(BaseCollector):
    """국방전자조달(d2b) 입찰공고 수집기 — 목록 5종."""

    source_name = "국방전자조달"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        now = datetime.now()
        max_pages = kwargs.get("max_pages", DEFAULT_MAX_PAGES)
        notices: list[Notice] = []
        errors: list[str] = []
        pages = 0
        skips: Counter[str] = Counter()

        async with create_client(timeout=30.0) as client:
            for spec in LISTS:  # 목록 하나가 실패해도 나머지 목록은 받는다 — 실패는 그 목록 이름과 함께 errors로
                items, n, errs = await fetch_pages(
                    client, BASE_URL + spec.operation,
                    {"serviceKey": self.api_key, **_date_params(spec, days, now)}, parse_xml,
                    rows=ROWS, max_pages=max_pages, label=f"[국방전자조달 {spec.kind}]", mask=self._mask,
                )
                pages += n
                errors.extend(errs)
                for item in items:
                    try:
                        notices.append(_item_to_notice(item, spec))
                    except Exception as e:
                        self._record_skip(skips, e, item)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages, errors

    async def health_check(self) -> dict:
        start = time.time()
        try:
            spec = LISTS[0]
            async with create_client(timeout=15.0) as client:
                resp = await client.get(BASE_URL + spec.operation, params={
                    "serviceKey": self.api_key, "pageNo": "1", "numOfRows": "1",
                    **_date_params(spec, 7, datetime.now()),
                })
                resp.raise_for_status()
                parse_xml(resp.content)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _item_to_notice(item, spec: ListSpec) -> Notice:
    def t(tag: str) -> str:
        return (item.findtext(tag) or "").strip()

    title = " ".join(t(spec.title).split())
    key_parts = {f: t(f) for f in spec.key}
    order = t(spec.order)
    require_fields(**{spec.title: title}, **key_parts, **{spec.order: order})
    start_str = parse_date(t(spec.start)) if spec.start else None
    end_str = parse_date(t(spec.end))  # "202609281000"
    return Notice(
        source="국방전자조달",
        bid_no=f"D2B-{spec.kind}-{''.join(key_parts.values())}-{order}",
        title=title,
        organization=t("ornt") if spec.kind != "국외경쟁" else FOREIGN_ORGANIZATION,
        start_date=start_str or None,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=SITE_URL,
        detail_url="",
        budget=None,  # 기초예비가격·예산금액 등은 extra 원문(2026-09-26 사용자 — 이름별 표시는 BidWatch)
        category=t("busiDivs"),
        extra=raw_fields(item),
    )
