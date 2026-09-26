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

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

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


@dataclass(frozen=True)
class DetailSpec:
    operation: str
    params: tuple[str, ...]       # 상세 필수 파라미터 — 목록 행의 같은 이름 필드 값
    lookup: str | None            # bid_no로 복원할 수 없는 값이 있으면: 그 값을 얻을 목록 조회 파라미터(키 필드)


# 상세 5종(v1.5.0, 2026-09-26 실측 — scripts/_tmp/d2b_dtl/). 경쟁 2종은 bid_no 키에서 파라미터를 전부 복원한다.
# 시설경쟁 pblancSeCode·국내수의 iemNo·ntatPlanDate·시설수의 ntatPlanDate는 bid_no에 없고 틀리면 빈 응답이라(추측 시도로 판정 불가)
# 목록 오퍼레이션을 키로 1회 조회해 같은 키·차수 행에서 얻는다(2026-09-26 사용자 — 건당 2회, 목록 한도를 collect와 나눠 씀).
DETAILS = {
    "국내경쟁": DetailSpec("getDmstcCmpetBidPblancDetail", ("demandYear", "orntCode", "pblancNo", "dcsNo", "pblancOdr"), None),
    "국외경쟁": DetailSpec("getOutnatnCmpetBidPblancDetail", ("pblancYear", "pblancNo", "dcsNo", "groupNo", "pblancOdr"), None),
    "시설경쟁": DetailSpec("getFcltyCmpetBidPblancDetail",
                        ("pblancYear", "orntCode", "pblancNo", "cntrwkNo", "pblancSeCode", "pblancOdr"), "g2bPblancNo"),
    "국내수의": DetailSpec("getDmstcOthbcVltrnNtatPlanDetail",
                        ("demandYear", "orntCode", "pblancNo", "dcsNo", "iemNo", "pblancOdr", "ntatPlanDate"), "pblancOrDcsNo"),
    "시설수의": DetailSpec("getFcltyOthbcVltrnNtatPlanDetail",
                        ("orntCode", "pblancNo", "cntrwkNo", "pblancOdr", "ntatPlanDate"), "pblancNoOrCntrwkNo"),
}
# 경쟁 2종 키 분해 — 목록 표본 257행 전부 이 구성(pblancNo 7자, 국외는 22자 고정). 발주기관 코드 = pblancNo 앞 3자(1,136/1,136행)
_DOMESTIC_KEY = re.compile(r"^(\d{4})([A-Z0-9]{7})([A-Z0-9]+)$")
_FOREIGN_KEY = re.compile(r"^(\d{4})([A-Z0-9]{7})([A-Z0-9]{8})([A-Z0-9]{3})$")
LOOKUP_WINDOW_DAYS = 730  # 수의 목록은 견적서 마감 범위가 사실상 필수(빼면 0건) — 앞뒤로 넓게


def _split_bid_no(bid_no: str) -> tuple[ListSpec, str, str]:
    """`D2B-{구분}-{키}-{차수}` → (목록 명세, 키, 차수). 키에 `-`가 들어갈 수 있다(시설 공사번호 2026-15108)."""
    body = bid_no.removeprefix("D2B-") if bid_no.startswith("D2B-") else ""
    kind, _, rest = body.partition("-")
    key, _, order = rest.rpartition("-")
    spec = next((s for s in LISTS if s.kind == kind), None)
    if spec is None or not key or not order.isdigit():
        raise ValueError(f"국방전자조달 bid_no 형식이 아님(D2B-{{구분}}-{{키}}-{{차수}}): {bid_no!r}")
    return spec, key, order


def _competitive_params(kind: str, key: str, order: str, bid_no: str) -> dict:
    if kind == "국내경쟁" and (m := _DOMESTIC_KEY.match(key)):
        year, pblanc_no, dcs_no = m.groups()
        return {"demandYear": year, "orntCode": pblanc_no[:3], "pblancNo": pblanc_no, "dcsNo": dcs_no, "pblancOdr": order}
    if kind == "국외경쟁" and (m := _FOREIGN_KEY.match(key)):
        year, pblanc_no, dcs_no, group_no = m.groups()
        return {"pblancYear": year, "pblancNo": pblanc_no, "dcsNo": dcs_no, "groupNo": group_no, "pblancOdr": order}
    raise ValueError(f"국방전자조달 bid_no 키를 상세 파라미터로 나눌 수 없음 — 목록 형식 변경 의심: {bid_no!r}")


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

    async def fetch_detail(self, bid_no: str) -> dict:
        """공고 1건 상세 (v1.5.0) — 추정가격·낙찰하한율·담당자·지역/면허 제한 등. 공식 API 상세 오퍼레이션 5종.

        호출 수: 국내·국외경쟁 1회, 시설경쟁·국내수의·시설수의 2회(목록 조회 1 + 상세 1 — 상세 필수 값이 bid_no에 없다).
        **오퍼레이션당 100회/일**을 목록 수집(`collect`)과 나눠 쓴다. 수집 경로에서는 부르지 않는다.

        Returns: 상세 `item`의 비어 있지 않은 필드 전부·원래 이름(`areaLmttList` 등 `^` 구분 문자열도 원문 그대로)
            + `attachments: []`·`content: ""`(상세에 첨부·본문 필드가 없다).
        Raises: 목록에서 같은 키·차수 행을 못 찾음, 상세 item 0개(없는 공고와 파라미터 불일치가 같은 응답 — 구분 불가),
            `resultCode` ≠ 00, HTTP 오류 → 예외(메시지의 API 키는 가린다).
        """
        spec, key, order = _split_bid_no(bid_no)
        detail = DETAILS[spec.kind]
        try:
            async with create_client(timeout=15.0) as client:
                if detail.lookup is None:
                    params = _competitive_params(spec.kind, key, order, bid_no)
                else:
                    params = await self._lookup_params(client, spec, detail, key, order, bid_no)
                resp = await client.get(BASE_URL + detail.operation, params={"serviceKey": self.api_key, **params})
                resp.raise_for_status()
                items, _ = parse_xml(resp.content)
        except httpx.HTTPError as e:
            # httpx 예외 문자열에 serviceKey가 든 URL이 실린다 — 원 예외를 체인에 남기지 않는다(from None)
            raise RuntimeError(self._mask(f"국방전자조달 상세 {bid_no}: {type(e).__name__}: {e}")) from None
        except ValueError as e:
            raise ValueError(self._mask(f"국방전자조달 상세 {bid_no}: {e}")) from None
        if not items:
            raise ValueError(f"국방전자조달 상세 {bid_no}: 결과 없음 — 없는 공고이거나 파라미터 불일치"
                             f"(d2b는 둘을 같은 빈 응답으로 준다) {params}")
        if len(items) > 1:
            raise ValueError(f"국방전자조달 상세 {bid_no}: item {len(items)}개 — 응답 형식 변경 의심")
        out = raw_fields(items[0]) or {}
        if clash := sorted(out.keys() & {"attachments", "content"}):
            raise ValueError(f"국방전자조달 상세 {bid_no}: 원문 필드가 표준 키와 겹침 {clash} — 응답 형식 변경 의심")
        out["attachments"] = []
        out["content"] = ""
        return out

    async def _lookup_params(self, client, spec: ListSpec, detail: DetailSpec, key: str, order: str, bid_no: str) -> dict:
        """목록을 키로 1회 조회해 같은 키·차수 행의 필드로 상세 파라미터를 만든다."""
        if spec.kind == "시설경쟁":
            query = {"g2bPblancNo": key}  # 날짜 없이 그 공고의 모든 차수가 온다(실측)
        else:
            pblanc_no = key[4:11] if spec.kind == "국내수의" else key[:7]  # 키 = 연도+공고번호+판단번호 / 공고번호+공사번호
            now = datetime.now()
            query = {detail.lookup: pblanc_no,
                     "prqudoPresentnClosDateBegin": (now - timedelta(days=LOOKUP_WINDOW_DAYS)).strftime("%Y%m%d"),
                     "prqudoPresentnClosDateEnd": (now + timedelta(days=LOOKUP_WINDOW_DAYS)).strftime("%Y%m%d")}
        resp = await client.get(BASE_URL + spec.operation,
                                params={"serviceKey": self.api_key, "pageNo": "1", "numOfRows": str(ROWS), **query})
        resp.raise_for_status()
        rows, total = parse_xml(resp.content)
        for row in rows:
            if (("".join((row.findtext(f) or "").strip() for f in spec.key) == key)
                    and (row.findtext(spec.order) or "").strip() == order):
                params = {p: (row.findtext(p) or "").strip() for p in detail.params}
                if missing := [p for p, v in params.items() if not v]:
                    raise ValueError(f"목록 행에 상세 파라미터 없음 {missing} — 응답 형식 변경 의심")
                return params
        raise ValueError(f"목록({spec.operation})에서 같은 키·차수 행을 못 찾음 — {len(rows)}행 조회"
                         f"{f'(전체 {total}건 중 일부)' if total > len(rows) else ''}, 없는 공고이거나 목록 형식 변경")

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
