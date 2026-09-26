"""한국가스공사 입찰정보 수집기 (F-012, v1.4.0).

API: GET https://apis.data.go.kr/B551210/bidInfoList2/getBidInfoList2 (data.go.kr 15157366)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요
응답: XML(UTF-8). 필드 14개 — NOTICE_CODE(10자리)·NOTICE_NAME·WORK_TYPE_NAME·CONT_METHOD_NAME·NOTICE_DT·END_DT·CANCEL_YN…
날짜: DOCDATE_START/END = 공고일(NOTICE_DT) 범위. **날짜를 빼거나 파라미터 이름이 틀려도 00 + 0건**이 온다(2026-09-25 실측) —
      에러와 0건을 구분할 수 없으니 두 날짜를 늘 보낸다. numOfRows 상한 없음(2000 요청에 1,011건).
알리오엔 가스공사 공고의 약 1/6만 올라온다(9/22~23 API 36건 중 6건) — 이 수집기가 필요한 이유.
취소는 공고일 그대로 CANCEL_YN=취소로만 표시된다(extra 원문).
"""

import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import fetch_pages, parse_xml
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

API_URL = "https://apis.data.go.kr/B551210/bidInfoList2/getBidInfoList2"
ROWS = 1000
DEFAULT_MAX_PAGES = 20
ORGANIZATION = "한국가스공사"  # 단일 기관 API라 응답에 기관 필드가 없다 — 알리오 pname과 같은 이름
# 가스공사 전자입찰 상세 — 알리오 refrUrl 11건 전부 bid_code=001·round=01(2026-09-25). API 응답엔 두 값이 없다.
DETAIL_URL = "https://bid.kogas.or.kr:9443/supplier/contents/bid/bid_detail_view_notice.jsp"
SITE_ORIGIN = "https://bid.kogas.or.kr:9443"
# 상세(v1.5.0)는 공식 API가 아니라 이 화면 HTML을 읽는다 — 사이트 개편 시 깨지며 그때는 예외로 드러난다.
# bid_code·round는 001/01 외에는 400(2026-09-26 실측 26건 — 재공고는 새 notice_code를 받는다).
NOT_FOUND_TEXT = "정보가 존재하지 않습니다"  # 없는 공고: HTTP 200 + 이 alert만 든 159바이트


class KogasCollector(BaseCollector):
    """한국가스공사 입찰정보 수집기."""

    source_name = "가스공사"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            "serviceKey": self.api_key,
            "DOCDATE_START": start.strftime("%Y%m%d"),
            "DOCDATE_END": end.strftime("%Y%m%d"),
        }
        async with create_client(timeout=30.0) as client:
            items, pages, errors = await fetch_pages(
                client, API_URL, params, parse_xml,
                rows=ROWS, max_pages=kwargs.get("max_pages", DEFAULT_MAX_PAGES),
                label="[가스공사]", mask=self._mask,
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

    async def fetch_detail(self, bid_no: str) -> dict:
        """공고 1건 상세 (v1.5.0) — 추정가격·계약방법·담당자·입찰 진행순서·품목·첨부. 목록 API(필드 14개)엔 금액이 없다.

        Returns: 화면 항목명 → 값(공백 정리한 원문 텍스트, 빈 값 제외, 같은 항목명이 다시 나오면 list) +
            `진행상태`·`진행안내`(화면 상단 진행 단계·안내 문구 — 취소 공고는 안내 문구에만 "취소"가 나온다) +
            `품목내역`(품목표 — 열 제목을 키로 한 list[dict]) + `attachments`(페이지의 내려받기 링크 전부, 순서대로 —
            공고 첨부 `bid_download_attfile`·표준 계약조건 `bid_download_rule_proc`·구매요청 첨부) + `content`("" — 본문 필드 없음).
        Raises: HTTP 오류(파라미터 오류는 400), "정보가 존재하지 않습니다"(없는 공고), 공고번호 칸이 요청 번호와 다르거나
            건명이 없음(화면 개편 의심) → 예외.
        """
        code = bid_no.removeprefix("KOGAS-") if bid_no.startswith("KOGAS-") else ""
        if not code.strip():
            raise ValueError(f"가스공사 bid_no 형식이 아님(KOGAS-{{NOTICE_CODE}}): {bid_no!r}")

        async with create_client(timeout=15.0) as client:
            resp = await client.get(DETAIL_URL, params={"notice_code": code, "bid_code": "001", "round": "01"})
            resp.raise_for_status()
        return _parse_detail(resp.content.decode("cp949", errors="replace"), code, bid_no)

    async def health_check(self) -> dict:
        start = time.time()
        try:
            today = datetime.now()
            async with create_client(timeout=15.0) as client:
                resp = await client.get(API_URL, params={
                    "serviceKey": self.api_key, "pageNo": "1", "numOfRows": "1",
                    "DOCDATE_START": (today - timedelta(days=14)).strftime("%Y%m%d"),
                    "DOCDATE_END": today.strftime("%Y%m%d"),
                })
                resp.raise_for_status()
                parse_xml(resp.content)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).replace("\xa0", " ").split())


def _parse_detail(html: str, code: str, bid_no: str) -> dict:
    """상세 HTML → fetch_detail 반환 dict. 항목 표는 `td.t_g`(항목명) 바로 다음 `td`(값) 쌍이다(2026-09-26 표본 26건)."""
    if NOT_FOUND_TEXT in html:
        raise ValueError(f"가스공사 상세 {bid_no}: 없는 공고(\"{NOT_FOUND_TEXT}\")")
    soup = BeautifulSoup(html, "lxml")
    detail: dict = {}
    for label in soup.select("td.t_g"):
        if label.find_parent("td", class_="c"):
            continue  # 값 칸 안의 중첩 항목(면허 업종그룹 등) — 바깥 항목의 값에 이미 들어 있다
        value_td = label.find_next_sibling("td")
        key, value = _text(label), _text(value_td) if value_td else ""
        if not key or not value:
            continue
        if key in detail:  # 표본엔 없었다 — 버리지 않고 모은다
            prev = detail[key]
            detail[key] = (prev if isinstance(prev, list) else [prev]) + [value]
        else:
            detail[key] = value

    if detail.get("공고번호") != code or not detail.get("건명"):
        raise ValueError(f"가스공사 상세 {bid_no}: 공고번호 칸={detail.get('공고번호')!r}·건명 없음 여부={not detail.get('건명')} "
                         f"— 화면 개편 의심")
    for key, selector in (("진행상태", "td.st_c"), ("진행안내", "td.st_t")):
        if (el := soup.select_one(selector)) and (v := _text(el)):
            detail[key] = v
    if items := _item_rows(soup):
        detail["품목내역"] = items
    detail["content"] = ""
    detail["attachments"] = [{"name": _text(a), "url": urljoin(SITE_ORIGIN, a["href"])}
                             for a in soup.select("a[href]") if "/bid_download" in a["href"]]
    return detail


def _item_rows(soup) -> list[dict]:
    """품목표(#itempanel) — 첫 행의 `td.t_c`가 열 제목(업무마다 열이 다르다 — 표본 3가지)."""
    table = soup.select_one("#itempanel table")
    if table is None:
        return []
    rows = table.find_all("tr")
    headers = [_text(td) for td in rows[0].find_all("td")] if rows else []
    out = []
    for tr in rows[1:]:
        cells = []
        for td in tr.find_all("td"):  # 용역은 품목 아래 colspan 하위 행(서비스 내역)이 붙는다 — 병합 칸을 펼쳐 열을 맞춘다
            cells += [_text(td)] + [""] * (int(td.get("colspan") or 1) - 1)
        if len(cells) != len(headers):
            raise ValueError(f"가스공사 품목표 열 수 불일치(제목 {len(headers)}·행 {len(cells)}) — 화면 개편 의심")
        row = {h: c for h, c in zip(headers, cells) if c}
        if row:
            out.append(row)
    return out


def _item_to_notice(item) -> Notice:
    def t(tag: str) -> str:
        return (item.findtext(tag) or "").strip()

    code = t("NOTICE_CODE")
    title = " ".join(t("NOTICE_NAME").split())
    start_str = parse_date(t("NOTICE_DT"))
    require_fields(NOTICE_CODE=code, NOTICE_NAME=title, NOTICE_DT=start_str)
    end_str = parse_date(t("END_DT"))  # "2026-09-30 10:00"
    url = f"{DETAIL_URL}?notice_code={code}&bid_code=001&round=01"
    return Notice(
        source="가스공사",
        bid_no=f"KOGAS-{code}",
        title=title,
        organization=ORGANIZATION,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
        budget=None,  # 응답에 예산 필드가 없다(낙찰금액 SUCCESS_AMT뿐)
        category=t("WORK_TYPE_NAME"),
        extra=raw_fields(item),
    )
