"""한국수자원공사 전자조달 입찰공고 수집기 (F-014, v1.4.0).

API: GET https://apis.data.go.kr/B500001/ebid/tndr3/{cntrwkList,servcList,gdsList,dmscptList} (data.go.kr 15101635)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요
응답: JSON(_type=json). 날짜 필터는 **월 단위 searchDt=YYYYMM(공고일 기준) 하나뿐** → 기준일이 든 달부터 이번 달까지 받아
      공고일(tndrPblancDe, 정수)로 거른다.
실측 함정(2026-09-25~26, docs/institution_sources.md):
- 응답이 크면(용역 142건 53KB는 됨, 발주계획 물품 411건은 안 됨) HTTP 200 + 본문 0바이트(에러 코드·건수 없음).
- **페이지 정렬이 불안정하다** — 50건씩 나눠 받으면 같은 행이 1·2페이지에 모두 나오고, 전체 건수는 그대로라 그만큼 다른 공고가
  빠진다(2026-09-26 용역 142건 중 4건). → 먼저 한 페이지(1000건)로 전량을 받고, 빈 본문이면 나눠 받되 겹친 행 수를 errors로 알린다.
- 에러와 0건을 구분할 수 없다 — searchDt 누락·미래 월·범위 밖 페이지 모두 00 + totalCount 0 + items "" → 요청 인자를 코드로 보장.
- items는 0건이면 "", 1건이면 {"item": dict}, 여러 건이면 {"item": list}.
- 취소된 공고는 API에서 빠진다(알리오 [취소공고]만 남는다). 공고번호에 차수가 없다 — 재공고는 새 번호 + 제목 "(재공고)".
범위 밖: 사전규격(stndrd3)·발주계획(ordg2)은 공고번호 필드가 없어 bid_no 설계가 필요하다, 입찰결과(rstList).
"""

import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import EMPTY_BODY_MSG, fetch_pages
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

logger = logging.getLogger("bid_collectors")

BASE_URL = "https://apis.data.go.kr/B500001/ebid/tndr3/"
OPERATIONS = ("cntrwkList", "servcList", "gdsList", "dmscptList")  # 공사·용역·물품·내자
FULL_ROWS = 1000  # 한 페이지 전량 — 정렬 불안정으로 인한 겹침·누락이 없다
ROWS = 50  # 전량이 빈 본문일 때 나눠 받는 크기(100건 응답이 약 40KB)
DEFAULT_MAX_PAGES = 20
ORGANIZATION = "한국수자원공사"  # 단일 기관 API — 알리오 pname과 같은 이름(계약 부서는 extra의 cntrctDeptNm)
DETAIL_URL = "https://ebid.kwater.or.kr/fz"  # 알리오 refrUrl의 bidno= 링크(2026-09-25 확인)
# 상세(v1.5.0) — 공식 API가 아니라 사이트(WebSquare) 화면이 부르는 내부 JSON. 사이트 개편 시 깨지며 그때는 예외로 드러난다.
DETAIL_API_URL = "https://ebid.kwater.or.kr/bidpblanc/bidpblancsttus/selectBidPblancDtl.do"
# 첨부 내려받기 — 사이트 JS bid_cmmn.js의 bid.download가 쓰는 경로(2026-09-26 3건 실제로 받음, 세션 불필요)
ATTACH_URL = "https://ebid.kwater.or.kr/sc/file/downloadAtchFileOne.do"


def parse_json(content: bytes) -> tuple[list[dict], int]:
    """JSON 응답 → (항목 목록, totalCount). 실패는 ValueError."""
    body = content.strip()
    if not body:
        raise ValueError(f"{EMPTY_BODY_MSG} — 응답 크기 초과 등 서버 오류 의심")
    data = json.loads(body)
    resp = data.get("response") if isinstance(data, dict) else None
    if not isinstance(resp, dict):
        raise ValueError("응답 형식 이상 — response 없음")
    code = str((resp.get("header") or {}).get("resultCode", "")).strip()
    if code != "00":
        raise ValueError(f"API 에러: {code} - {(resp.get('header') or {}).get('resultMsg')}")
    b = resp.get("body") or {}
    items = b.get("items")
    if items in ("", None):
        items = []
    elif isinstance(items, dict):
        item = items.get("item")
        items = [item] if isinstance(item, dict) else (item or [])
    if not isinstance(items, list):
        raise ValueError(f"응답 형식 이상 — items가 {type(items).__name__}")
    return items, int(b.get("totalCount") or 0)


def _months(cutoff: date, today: date) -> list[str]:
    """기준일이 든 달부터 이번 달까지 YYYYMM."""
    out, y, m = [], cutoff.year, cutoff.month
    while (y, m) <= (today.year, today.month):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class KwaterCollector(BaseCollector):
    """한국수자원공사 입찰공고 수집기 — 공사·용역·물품·내자."""

    source_name = "수자원공사"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        today = datetime.now().date()
        cutoff = today - timedelta(days=days)
        max_pages = kwargs.get("max_pages", DEFAULT_MAX_PAGES)
        notices: list[Notice] = []
        errors: list[str] = []
        pages = 0
        skips: Counter[str] = Counter()

        async with create_client(timeout=30.0) as client:
            for op in OPERATIONS:
                for month in _months(cutoff, today):  # 한 오퍼레이션·한 달이 실패해도 나머지는 받는다
                    label = f"[수자원공사 {op} {month}]"
                    params = {"serviceKey": self.api_key, "searchDt": month, "_type": "json"}
                    items, n, errs = await fetch_pages(client, BASE_URL + op, params, parse_json, rows=FULL_ROWS,
                                                       max_pages=max_pages, label=label, mask=self._mask)
                    if n == 0 and errs and EMPTY_BODY_MSG in errs[0]:
                        # 한 페이지로 받기엔 크다 — 나눠 받는다. 정렬이 불안정해 겹친 행만큼 다른 공고가 빠질 수 있다
                        logger.warning(f"{label} 전량 한 페이지가 빈 본문 — {ROWS}건씩 나눠 받는다")
                        items, n, errs = await fetch_pages(client, BASE_URL + op, params, parse_json, rows=ROWS,
                                                           max_pages=max_pages, label=label, mask=self._mask)
                        overlap = len(items) - len({i.get("tndrPbanno") for i in items})
                        if overlap:
                            errs.append(f"{label} 페이지 정렬 불안정 — {len(items)}행 중 {overlap}행이 겹침, 같은 수만큼 공고를 "
                                        f"못 받았을 수 있음(한 페이지 전량은 응답 크기 초과로 빈 본문)")
                    pages += n
                    errors.extend(errs)
                    for item in items:
                        try:
                            notice = _item_to_notice(item)
                        except Exception as e:
                            self._record_skip(skips, e, item)
                            continue
                        if notice.start_date >= cutoff:  # 월 단위로 받은 것 중 기준일 이후만
                            notices.append(notice)

        if skip_msg := self._skip_message(skips):
            errors.append(skip_msg)
        return notices, pages, errors

    async def fetch_detail(self, bid_no: str) -> dict:
        """공고 1건 상세 (v1.5.0) — 요청금액·입찰 일정·담당자·첨부 등 목록 API(필드 12개)에 없는 것. 수집 경로에서는 부르지 않는다.

        Returns: `data.tndrPblanc`의 비어 있지 않은 필드 전부·원래 이름 + `data`의 나머지 필드 원문 그대로(입찰 일정
            `tndrPrgsOrdrList`·첨부 `atchflList` 포함) + `attachments`(`[{"name": docFileNm, "url"}]`, 없으면 []) + `content`("" — 본문 필드 없음,
            공고문은 첨부 hwp).
        Raises: `message.code`가 success가 아니면(요청 형식 오류·서버 오류) 예외, success인데 `tndrPblanc`가 null이면
            (없는 공고번호도 success로 온다 — 2026-09-26 실측) 예외.
        """
        pbanno = bid_no.removeprefix("KWATER-") if bid_no.startswith("KWATER-") else ""
        if not pbanno.strip():
            raise ValueError(f"수자원공사 bid_no 형식이 아님(KWATER-{{tndrPbanno}}): {bid_no!r}")

        async with create_client(timeout=15.0) as client:
            resp = await client.post(DETAIL_API_URL, json={"dmaSearchData": {"tndrPbanno": pbanno}})
            resp.raise_for_status()
            body = resp.json()
        return _parse_detail(body, bid_no)

    async def health_check(self) -> dict:
        start = time.time()
        try:
            async with create_client(timeout=15.0) as client:
                resp = await client.get(BASE_URL + OPERATIONS[1], params={
                    "serviceKey": self.api_key, "pageNo": "1", "numOfRows": "1", "_type": "json",
                    "searchDt": datetime.now().strftime("%Y%m"),
                })
                resp.raise_for_status()
                parse_json(resp.content)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _parse_detail(body, bid_no: str) -> dict:
    """상세 응답 JSON → fetch_detail 반환 dict. 형식이 다르면 ValueError."""
    message = body.get("message") if isinstance(body, dict) else None
    code = message.get("code") if isinstance(message, dict) else None
    if code != "success":
        name = message.get("code_name") if isinstance(message, dict) else None
        raise ValueError(f"수자원공사 상세 {bid_no}: code={code!r} {name!r} — 요청 형식 오류 또는 서버 오류")
    data = body.get("data")
    pblanc = data.get("tndrPblanc") if isinstance(data, dict) else None
    if not isinstance(pblanc, dict):
        raise ValueError(f"수자원공사 상세 {bid_no}: tndrPblanc 없음 — 없는 공고번호(success로 온다) 또는 응답 형식 변경")
    files = data.get("atchflList")
    if files is None:
        files = []
    if not isinstance(files, list):
        raise ValueError(f"수자원공사 상세 {bid_no}: atchflList가 list가 아님({type(files).__name__}) — 응답 형식 변경 의심")

    detail = raw_fields(pblanc) or {}
    rest = raw_fields({k: v for k, v in data.items() if k != "tndrPblanc"}) or {}
    if clash := sorted(detail.keys() & rest.keys()):
        raise ValueError(f"수자원공사 상세 {bid_no}: tndrPblanc와 data 키 충돌 {clash} — 응답 형식 변경 의심")
    detail.update(rest)
    detail["content"] = ""
    detail["attachments"] = [
        {"name": f.get("docFileNm"),
         "url": f"{ATTACH_URL}?{urlencode({'xmlValue': json.dumps({'atchflId': f.get('atchflId'), 'fileSeq': f.get('fileSeq')}, separators=(',', ':'))})}"}
        for f in files
    ]
    return detail


def _item_to_notice(item: dict) -> Notice:
    def t(key: str) -> str:
        v = item.get(key)
        return "" if v is None else str(v).strip()  # 공고일·마감일은 정수로 온다(20260923)

    bid_no = t("tndrPbanno")
    title = " ".join(t("tndrPblancNm").split())
    start_str = parse_date(t("tndrPblancDe"))
    require_fields(tndrPbanno=bid_no, tndrPblancNm=title, tndrPblancDe=start_str)
    end_str = parse_date(t("tndrPblancEnddt"))  # 절반가량이 "-" → None
    url = f"{DETAIL_URL}?bidno={bid_no}"
    return Notice(
        source="수자원공사",
        bid_no=f"KWATER-{bid_no}",
        title=title,
        organization=ORGANIZATION,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
        budget=None,  # tndrPlnprc는 0(=미공개)이 절반 — 금액 해석은 원칙 ②, 원문은 extra(2026-09-26 사용자)
        category=t("cntrctDivNm"),
        extra=raw_fields(item),
    )
