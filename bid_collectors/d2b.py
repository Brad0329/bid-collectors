"""국방전자조달(d2b, 방위사업청) 입찰공고 수집기 (F-013, v1.4.0).

API: https://apis.data.go.kr/1690000/BidPblancInfoService/{목록 5종} (data.go.kr 15158416)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요. **오퍼레이션당 100회/일**(응답 헤더 x-ratelimit 실측)
응답: XML — JSON은 dcsNo가 int/str로 섞여 온다(2026-09-25 실측 136/6, 앞자리 0 유실 우려).
알리오에 없는 공고다(평일 목록 5종 합 하루 약 130건).

목록 5종과 날짜 기준(실측 — docs/institution_sources.md):
- 국내경쟁·시설경쟁: 공고일 범위(anmtDateBegin/End). 목록은 개찰 전 공고만 들고 있다.
- 국외경쟁: 개찰일(opengDateBegin/End)이 필수(빼도 에러 없이 anmt 조건만 먹는다) → 공고일 범위 + 개찰일 공고일 범위 시작~2년 뒤
  (v1.6.0 — 끝을 1년에서 2년으로. 개찰은 공고 뒤라 시작은 거르지 않는다 — 1년 전으로 넓히면 오류 없이 0건, 실측). 목록에 공고일·발주기관 필드가 없다 → start_date None,
  organization ""(종전 "방위사업청" 상수 — v1.6.0 원칙 ②).
- 국내·시설 공개수의협상: 공고일 필터도 공고일 필드도 없다 → 견적서 제출마감 (오늘 - days) ~ 1년 뒤, start_date None
  (v1.6.0 — 종전 "오늘부터"는 수집 기간 안에 마감된 공고를 걸렀다, 원칙 ②).
날짜 형식이 틀려도 00 + 0건이 온다 — 형식은 코드로 보장한다(YYYYMMDD).

url(v1.6.0): 목록 API에 상세 링크가 없어 사이트 상세 화면 GET 주소를 목록 원문 필드로 만든다(**비공식 경로** — bidwatch 요청서
bid-collectors_d2b_detail_url.md, 2026-09-26 실측 418행 쿠키 없이 412 열림 `scripts/_tmp/d2b_url/`). 메뉴 파라미터(pageDivs 등)가
없으면 첫 클릭(새 세션)에 500이다. 지명경쟁은 로그인이 필요해 구분별 목록 화면으로, 필드가 비어 만들 수 없으면 첫 화면 + errors.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import fetch_pages, parse_xml
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import CANCEL_KIND, determine_status

BASE_URL = "https://apis.data.go.kr/1690000/BidPblancInfoService/"
ROWS = 1000
DEFAULT_MAX_PAGES = 5
# 상세 링크 필드가 12개 오퍼레이션 어디에도 없다(스키마·응답 확인) — 상세 화면 주소를 만들 수 없을 때만 첫 화면으로
SITE_URL = "https://www.d2b.go.kr/"
SITE_ORIGIN = "https://www.d2b.go.kr"
OPEN_WINDOW_DAYS = 365  # 개찰일·견적서 마감 앞뒤 범위

# 상세 화면 GET 주소(v1.6.0) — 구분별 (경로, 메뉴 고정 파라미터, (파라미터, 원문 필드, 앞 n자)). 사이트 JS(index.js 220~387행)가
# 폼으로 넘기는 값과 같다. 고정 파라미터가 없으면 새 세션 첫 클릭에 500(국내경쟁 0/89·시설경쟁 0/37·수의 0/18, 2026-09-26 실측).
# 국외 grd_dprtCode·시설수의 ordr_year는 서버가 값을 검사하지 않지만(실측) JS와 같은 값을 넣는다.
DETAIL_PAGES = {
    "국내경쟁": ("/pdb/bid/bidAnnounceView.do", {"lv2Divs": "1", "pageDivs": "G1", "bid_divs": "bid"},
             (("dprt_code", "orntCode", None), ("anmt_divs", "pblancSeCode", None), ("anmt_numb", "pblancNo", None),
              ("rqst_degr", "pblancOdr", None), ("dcsn_numb", "dcsNo", None), ("rqst_year", "demandYear", None))),
    "시설경쟁": ("/peb/bid/announceView.do", {"lv2Divs": "1", "pageDivs": "E1"},
             (("dprt_code", "orntCode", None), ("anmt_divs", "pblancSeCode", None), ("anmt_numb", "pblancNo", None),
              ("rqst_degr", "pblancOdr", None), ("dcsn_numb", "cntrwkNo", None), ("rqst_year", "pblancYear", None))),
    "국외경쟁": ("/pcb/bid/bidAnnounceView.do", {"menuOption": "1"},
             (("grd_anmtYear", "pblancYear", None), ("grd_bidxDate", "opengDt", 8), ("grd_anmtNumb", "pblancNo", None),
              ("grd_dgNumb", "purchsRequstNo", None), ("grd_anmtRqst", "pblancOdr", None), ("grd_dprtCode", "pblancNo", 3),
              ("grd_dcsnNumb", "dcsNo", None), ("grd_gropNumb", "groupNo", None))),
    "국내수의": ("/pdb/openNego/openNegoPlanView.do", {"pageDivs": "G"},
             (("dmst_itnb", "iemNo", None), ("dcsn_numb", "dcsNo", None), ("negn_pldt", "ntatPlanDate", None),
              ("negn_degr", "pblancOdr", None), ("dprt_code", "orntCode", None), ("ordr_year", "demandYear", None),
              ("anmt_numb", "pblancNo", None))),
    "시설수의": ("/peb/openNego/openNegoPlanView.do", {"pageDivs": "E"},
             (("csrt_numb", "cntrwkNo", None), ("negn_pldt", "ntatPlanDate", None), ("dprt_code", "orntCode", None),
              ("ordr_year", "cntrwkNo", 4), ("negn_degr", "pblancOdr", None), ("anmt_numb", "pblancNo", None))),
}
# 지명경쟁(cntrctMth)은 상세가 로그인 필요(200 + alert "로그인 후 이용가능합니다." — 국내경쟁 2/2·시설경쟁 4/4 실측) →
# 구분별 목록 화면(쿠키 없이 200·로그인 문구 없음 확인, 목록 그리드 렌더링은 JS라 미확인)
NOMINATED = "지명경쟁"
NOMINATED_LIST_PAGES = {
    ("시설경쟁", None): SITE_ORIGIN + "/peb/bid/announceList.do?key=41",
    ("국내경쟁", "물품"): SITE_ORIGIN + "/pdb/bid/goodsBidAnnounceList.do?key=13",
    ("국내경쟁", "용역"): SITE_ORIGIN + "/psb/bid/serviceBidAnnounceList.do?key=32",
}
# 출처가 주는 취소 표시 — 경쟁 3종 pblancSe, 수의 2종 progrsSttus(bidwatch F-017과 같은 키·값)
NEGOTIATION_CANCEL = "공개협상취소"


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
        # 공고일 조건이 없어 견적서 마감으로 범위를 잡는다 — 수집 기간(days) 안에 마감된 공고도 받는다(v1.6.0)
        return {"prqudoPresentnClosDateBegin": begin, "prqudoPresentnClosDateEnd": ahead}
    params = {"anmtDateBegin": begin, "anmtDateEnd": today}
    if spec.dates == "anmt+openg":
        # 개찰일은 API가 요구해서 넣을 뿐 — 공고일 범위의 공고를 거르지 않게 한다(v1.6.0): 개찰은 공고 뒤라 시작은 공고일 범위와
        # 같게 두면 거르지 않고, 끝은 2년 뒤. **시작을 1년 전으로 넓히면 오류 없이 0건**(2026-09-26 실측: -365일 0건·-180일 4건)
        params |= {"opengDateBegin": begin, "opengDateEnd": (now + timedelta(days=2 * OPEN_WINDOW_DAYS)).strftime(fmt)}
    return params


def _detail_page_url(t, kind: str) -> str | None:
    """목록 행(원문 필드 읽기 함수 t)으로 사이트 상세 화면 주소. 필드가 비면 None — 호출자가 첫 화면으로 두고 errors에 센다."""
    if t("cntrctMth") == NOMINATED:
        return NOMINATED_LIST_PAGES.get((kind, None) if kind == "시설경쟁" else (kind, t("busiDivs")))
    path, fixed, fields = DETAIL_PAGES[kind]
    params = dict(fixed)
    for param, field, prefix in fields:
        value = t(field)
        if not value or (prefix and len(value) < prefix):
            return None
        params[param] = value[:prefix] if prefix else value
    return f"{SITE_ORIGIN}{path}?{urlencode(params)}"


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
        no_url: Counter[str] = Counter()

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
                        notice = _item_to_notice(item, spec)
                    except Exception as e:
                        self._record_skip(skips, e, item)
                        continue
                    notices.append(notice)
                    if notice.url == SITE_URL:
                        no_url[spec.kind] += 1

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        if no_url:  # 결측 0/418(2026-09-26) — 생기면 목록 형식 변경 신호다. 공고는 첫 화면 url로 그대로 돌려준다
            errors.append("[국방전자조달] 상세 화면 주소를 만들 수 없어 첫 화면으로 둔 공고 — "
                          + ", ".join(f"{k} {n}건" for k, n in no_url.items()) + " (주소 필드 결측·지명경쟁 업무 구분 미상)")
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
    cancelled = t("pblancSe") == CANCEL_KIND or t("progrsSttus") == NEGOTIATION_CANCEL
    return Notice(
        source="국방전자조달",
        bid_no=f"D2B-{spec.kind}-{''.join(key_parts.values())}-{order}",
        title=title,
        organization=t("ornt"),  # 국외경쟁 목록엔 발주기관 필드가 없어 빈 값(v1.6.0 — 종전 "방위사업청" 상수)
        start_date=start_str or None,
        end_date=end_str or None,
        status=determine_status(end_str, cancelled=cancelled),
        url=_detail_page_url(t, spec.kind) or SITE_URL,
        detail_url="",
        # 예산금액 budgetAmount 원문 한 필드(v1.6.0, 2026-09-26 사용자 — 수의 2종에만 있다, 실측 554/554). 경쟁 3종엔 예산 필드가 없어 None —
        # 기초예비가격 bsicExpt·기초금액 baseAmnt는 예산이 아니라 대체하지 않는다(extra 원문)
        budget=int(float(budget_raw)) if (budget_raw := t("budgetAmount")) else None,
        category=t("busiDivs"),
        extra=raw_fields(item),
    )
