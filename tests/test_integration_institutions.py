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
