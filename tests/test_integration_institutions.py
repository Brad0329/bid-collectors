"""자체조달 기관 수집기 4종 통합 테스트 — 실제 API 호출(F-011~F-014, v1.4.0).

기관마다: 최근 기간 1건 이상 · errors 없음 · **bid_no 중복 제거로 합쳐진 공고 0건**(total_fetched == total_after_dedup — d2b 차수·
수자원 페이지 겹침 결함을 잡은 기준, work_log/Phase_009.md) · 원문을 따로 받아 항목마다 extra 키 집합 == 비어 있지 않은 원문 필드 집합.
호출: 수집 LH 1·가스 1·d2b 5·수자원 4~8 + 원문 대조 4 + health 4.
"""

import json
import os
from datetime import datetime, timedelta

import httpx
import pytest
from dotenv import load_dotenv
from lxml import etree

load_dotenv()

from bid_collectors import D2bCollector, KogasCollector, KwaterCollector, LhCollector  # noqa: E402
from bid_collectors import d2b, kogas, kwater, lh  # noqa: E402

pytestmark = pytest.mark.integration

KEY = os.environ.get("DATA_GO_KR_KEY", "")
NOW = datetime.now()


def _ymd(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y%m%d")


def _xml_raw(content: bytes, key) -> dict[str, set[str]]:
    """테스트 쪽 독립 구현 — 원문 항목의 비어 있지 않은 태그 집합(raw_fields를 쓰지 않는다)."""
    root = etree.fromstring(content.strip())
    return {key(it): {c.tag for c in it if (c.text or "").strip()} for it in root.findall(".//item")}


async def _get(url: str, params: dict) -> bytes:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, params={"serviceKey": KEY, **params})
        resp.raise_for_status()
        return resp.content


def _assert_clean(result, min_count: int = 1) -> None:
    assert result.errors == [], f"수집 에러: {result.errors}"
    assert len(result.notices) >= min_count
    assert result.total_fetched == result.total_after_dedup, "서로 다른 원문 행이 같은 bid_no로 합쳐졌다"


def _assert_extra_matches(notices, raw: dict[str, set[str]], key_of) -> None:
    matched = 0
    for n in notices:
        k = key_of(n)
        if k in raw:
            assert set(n.extra) == raw[k], f"{n.bid_no}: extra ≠ 원문 비어 있지 않은 필드"
            matched += 1
    assert matched >= 1, "원문과 대조한 공고가 없다"


async def test_lh_real():
    result = await LhCollector().collect(days=7)
    _assert_clean(result)
    raw = _xml_raw(await _get(lh.API_URL, {"tndrbidRegDtStart": _ymd(7), "tndrbidRegDtEnd": _ymd(0),
                                           "numOfRows": "1000", "pageNo": "1"}),
                   key=lambda it: it.findtext("bidNum").strip())
    _assert_extra_matches(result.notices, raw, lambda n: n.bid_no.removeprefix("LH-"))
    assert (await LhCollector().health_check())["status"] == "ok"


async def test_kogas_real():
    result = await KogasCollector().collect(days=7)
    _assert_clean(result)
    raw = _xml_raw(await _get(kogas.API_URL, {"DOCDATE_START": _ymd(7), "DOCDATE_END": _ymd(0),
                                              "numOfRows": "1000", "pageNo": "1"}),
                   key=lambda it: it.findtext("NOTICE_CODE").strip())
    _assert_extra_matches(result.notices, raw, lambda n: n.bid_no.removeprefix("KOGAS-"))
    assert (await KogasCollector().health_check())["status"] == "ok"


async def test_d2b_real():
    result = await D2bCollector().collect(days=7)
    _assert_clean(result)
    kinds = {n.bid_no.split("-")[1] for n in result.notices}
    assert {"국내경쟁", "국내수의", "시설수의"} <= kinds  # 진행 중 수의협상은 늘 있다(실측 116·172건)
    spec = d2b.LISTS[0]
    raw = _xml_raw(await _get(d2b.BASE_URL + spec.operation, {"anmtDateBegin": _ymd(7), "anmtDateEnd": _ymd(0),
                                                             "numOfRows": "1000", "pageNo": "1"}),
                   key=lambda it: f"D2B-국내경쟁-{it.findtext('g2bPblancNo').strip()}-{it.findtext('pblancOdr').strip()}")
    _assert_extra_matches(result.notices, raw, lambda n: n.bid_no)
    assert (await D2bCollector().health_check())["status"] == "ok"


async def test_kwater_real():
    result = await KwaterCollector().collect(days=14)
    _assert_clean(result)
    latest = max(n.start_date for n in result.notices if n.category == "용역")  # 월초엔 이번 달이 비어 있을 수 있다
    body = json.loads(await _get(kwater.BASE_URL + "servcList", {"searchDt": latest.strftime("%Y%m"), "_type": "json",
                                                                 "numOfRows": "1000", "pageNo": "1"}))
    items = body["response"]["body"]["items"]
    items = [] if items == "" else items["item"] if isinstance(items["item"], list) else [items["item"]]
    raw = {it["tndrPbanno"]: {k for k, v in it.items() if v is not None and str(v).strip() != ""} for it in items}
    _assert_extra_matches(result.notices, raw, lambda n: n.bid_no.removeprefix("KWATER-"))
    assert (await KwaterCollector().health_check())["status"] == "ok"


# ── fetch_detail (v1.5.0) — 최근 공고로 상세를 부르고, 원천을 테스트 쪽에서 따로 받아 키·첨부 건수를 대조한다.
# 키 대조: JSON·XML 원천(수자원·d2b 국내경쟁)은 키 집합 일치, HTML 원천(가스·LH)은 핵심 칸 + 원문에서 따로 센 첨부 링크 수.
# 호출: LH 목록 1 + (검색·상세) 2 + 원문 1 / 가스 목록 1 + 상세 2 / d2b 목록 5 + 상세 6 + 목록 조회 3 / 수자원 목록 4~8 + 상세 2

def _nonempty(d: dict) -> set[str]:
    return {k for k, v in d.items() if v is not None and not (isinstance(v, str) and not v.strip())}


async def test_kwater_fetch_detail_real():
    notices = (await KwaterCollector().collect(days=14)).notices
    bid_no = notices[0].bid_no
    d = await KwaterCollector().fetch_detail(bid_no)
    async with httpx.AsyncClient(timeout=30.0) as client:  # 원천 독립 호출
        body = (await client.post(kwater.DETAIL_API_URL, json={"dmaSearchData": {"tndrPbanno": bid_no.removeprefix("KWATER-")}})).json()
    data = body["data"]
    expected = _nonempty(data["tndrPblanc"]) | _nonempty({k: v for k, v in data.items() if k != "tndrPblanc"})
    assert set(d) == expected | {"attachments", "content"}  # tndrPblanc 평탄화 + data 나머지 원문, 이름 그대로
    assert len(d["attachments"]) == len(data["atchflList"])
    assert d["tndrPbanno"] == bid_no.removeprefix("KWATER-")


async def test_kogas_fetch_detail_real():
    notices = (await KogasCollector().collect(days=7)).notices
    code = notices[0].bid_no.removeprefix("KOGAS-")
    d = await KogasCollector().fetch_detail(notices[0].bid_no)
    async with httpx.AsyncClient(timeout=30.0) as client:
        html = (await client.get(kogas.DETAIL_URL, params={"notice_code": code, "bid_code": "001", "round": "01"})).content
    assert d["공고번호"] == code and d["건명"]
    assert len(d["attachments"]) == html.count(b"/bid_download")  # 내려받기 링크 전부
    assert d["진행상태"] and isinstance(d.get("품목내역"), list)


async def test_lh_fetch_detail_real():
    notices = (await LhCollector().collect(days=14)).notices
    # 정정·취소로 차수가 오른 공고를 우선 — 최신 차수를 검색으로 찾는 경로를 잰다(없으면 아무거나)
    n = next((x for x in notices if x.extra.get("bidDegree") not in (None, "00")), notices[0])
    d = await LhCollector().fetch_detail(n.bid_no)
    bid_num = n.bid_no.removeprefix("LH-")
    degree = n.extra["bidDegree"]
    assert d["공고일반정보/입찰공고번호"] == f"{bid_num} - {degree}"  # 목록 API의 최신 차수와 같은 화면
    assert d["공고일반정보/입찰공고건명"]
    cmd = lh._JOB_CMD[{"시설공사": "10", "용역": "20", "물품": "30", "지급자재": "40"}[n.category]]
    async with httpx.AsyncClient(timeout=30.0, verify=lh.lh_ssl_context()) as client:  # 원문 독립 호출 — 첨부 링크를 따로 센다
        html = (await client.get(f"{lh.SITE}ebid.et.tp.cmd.{cmd}.dev", params={"bidNum": bid_num, "bidDegree": degree})).text
    assert len(d["attachments"]) == html.count("fn_dds_open('") >= 1


async def test_d2b_fetch_detail_real():
    """5종 각 1건 — 경쟁 2종은 bid_no만으로, 시설경쟁·수의 2종은 목록 조회 후 상세."""
    notices = (await D2bCollector().collect(days=30)).notices  # 국외·시설경쟁은 7일엔 없을 수 있다 — 30일이면 5종이 다 있다
    by_kind: dict[str, str] = {}
    for n in notices:
        by_kind.setdefault(n.bid_no.split("-")[1], n.bid_no)
    assert set(by_kind) == {s.kind for s in d2b.LISTS}, f"30일 목록의 구분: {sorted(by_kind)}"
    details = {}
    for kind, bid_no in by_kind.items():
        d = details[kind] = await D2bCollector().fetch_detail(bid_no)
        assert d["attachments"] == [] and d["content"] == ""
        assert d["pblancOdr"] == bid_no.rsplit("-", 1)[1], f"{kind}: 다른 차수의 상세"
        assert len(d) >= 15, f"{kind}: 필드 {len(d)}개"
    # 국내경쟁은 원천을 따로 받아 키 집합 대조(파라미터는 테스트 쪽에서 독립 분해)
    key, order = by_kind["국내경쟁"].removeprefix("D2B-국내경쟁-").rsplit("-", 1)
    raw = _xml_raw(await _get(d2b.BASE_URL + "getDmstcCmpetBidPblancDetail", {
        "demandYear": key[:4], "orntCode": key[4:7], "pblancNo": key[4:11], "dcsNo": key[11:], "pblancOdr": order}),
        key=lambda it: "only")
    assert set(details["국내경쟁"]) - {"attachments", "content"} == raw["only"]
