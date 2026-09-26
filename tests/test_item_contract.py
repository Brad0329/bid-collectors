"""항목 수준 공통 계약 — 모든 API 수집기에 같은 시나리오를 돌린다 (v1.2.4 Phase 005 R1·R2, v1.2.5 Phase 006 B·원칙 ①).

- 2건 중 1건의 필드가 null·형식 이상이면 나머지 1건은 반환되고, 건너뛴 건수와 사유가 errors 한 줄에 담긴다
- ID가 없는 항목은 `{접두사}-`로 합쳐지지 않고 건너뛰어 사유별 한 줄 + 건수로 보고된다
- 숫자 ID 0은 유효하다
- (v1.2.5 B) 선택 필드가 null인 항목은 건너뛰지 않고 그 필드만 비운 채 돌아온다
- (v1.2.5 원칙 ①) extra에는 응답 항목의 비어 있지 않은 필드 전부가 원래 이름 그대로 담긴다 — 모르는 필드까지, 별칭 없이

수집기별 복사 대신 한 곳에 둔 이유: 같은 결함을 한 수집기만 고치는 일이 3회 이상 반복됐다(debt-audit 2026-09-24).
`test_every_collector_has_a_case`가 새 수집기를 이 목록에 강제로 편입시킨다.
"""

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import httpx
import pytest
import respx

import bid_collectors
from bid_collectors import (
    AlioCollector, BaseCollector, BizinfoCollector, D2bCollector, KogasCollector, KstartupCollector, KwaterCollector,
    LhCollector, NaraCollector, SmesCollector, Subsidy24Collector,
)
from bid_collectors import alio, bizinfo, d2b, kogas, kstartup, kwater, lh, nara, smes, subsidy24

TODAY = datetime.now()


def _xml(items: list[dict]) -> bytes:
    """None 값은 태그를 빼서 표현한다(XML에는 null이 없다)."""
    body = "".join(
        "<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in it.items() if v is not None) + "</item>"
        for it in items
    )
    return (f"<response><header><resultCode>00</resultCode></header><body><items>{body}</items>"
            f"<totalCount>{len(items)}</totalCount></body></response>").encode()


def _mock_bizinfo(items):
    for it in items:
        it["totCnt"] = len(items)
    respx.get(bizinfo.API_URL).mock(return_value=httpx.Response(200, json={"jsonArray": items}))


def _mock_kstartup(items):
    respx.get(kstartup.API_URL).mock(return_value=httpx.Response(
        200, json={"data": items, "totalCount": len(items), "matchCount": len(items)}))


def _mock_subsidy24(items):
    respx.get(subsidy24.API_URL).mock(return_value=httpx.Response(
        200, json={"data": items, "totalCount": len(items), "matchCount": len(items)}))


def _mock_smes(items):
    respx.get(smes.API_URL).mock(return_value=httpx.Response(200, content=_xml(items)))


def _mock_nara(items):
    respx.get(url__startswith=nara.BASE_URL).mock(return_value=httpx.Response(200, content=_xml(items)))


def _mock_alio(items):
    def page(request):
        result = items if request.url.params["pageNo"] == "1" else []
        return httpx.Response(200, json={"status": "success", "data": {"result": result, "totalCnt": len(items)}})
    respx.get(alio.API_URL).mock(side_effect=page)


def _mock_lh(items):
    respx.get(lh.API_URL).mock(return_value=httpx.Response(200, content=_xml(items)))


def _mock_kogas(items):
    respx.get(kogas.API_URL).mock(return_value=httpx.Response(200, content=_xml(items)))


def _mock_d2b(items):
    for spec in d2b.LISTS:
        body = _xml(items) if spec.kind == "국내경쟁" else _xml([])
        respx.get(d2b.BASE_URL + spec.operation).mock(return_value=httpx.Response(200, content=body))


def _mock_kwater(items):
    def month(request):  # 기준일이 전월이면 두 달을 부른다 — 이번 달에만 항목을 싣는다
        mine = items if request.url.params["searchDt"] == TODAY.strftime("%Y%m") else []
        shaped = "" if not mine else {"item": mine}
        return httpx.Response(200, json={"response": {"header": {"resultCode": "00"},
                                                      "body": {"items": shaped, "totalCount": len(mine)}}})
    for op in kwater.OPERATIONS:
        route = respx.get(kwater.BASE_URL + op)
        route.mock(side_effect=month) if op == "servcList" else route.mock(
            return_value=httpx.Response(200, json={"response": {"header": {"resultCode": "00"},
                                                                "body": {"items": "", "totalCount": 0}}}))


@dataclass
class Case:
    collector: Callable[[], BaseCollector]
    mock: Callable[[list[dict]], None]
    item: Callable[[object], dict]      # ID → 정상 항목
    id_field: str
    title_field: str
    zero_id: object                     # 숫자 0 ID (JSON은 int, XML은 "0")
    optional_field: str                 # 선택 필드 — null이어도 항목은 살아야 한다(v1.2.5 B)
    collect_kwargs: dict = field(default_factory=dict)
    bad_format: dict | None = None      # 필수 필드가 아닌데 변환을 깨뜨리는 값(없으면 None)


CASES = {
    BizinfoCollector: Case(
        lambda: BizinfoCollector(api_key="k"), _mock_bizinfo,
        lambda i: {"pblancId": i, "pblancNm": f"공고{i}", "creatPnttm": TODAY.strftime("%Y-%m-%d"),
                   "excInsttNm": "기관"},
        "pblancId", "pblancNm", 0,
        "reqstBeginEndDe",  # v1.2.4까지 null이면 `"~" in None` TypeError로 유효 공고가 "TypeError 1건"으로 버려졌다
        bad_format={"pblancNm": ["제목이 list"]},  # 종전(v1.2.3)엔 pydantic 예외가 _fetch 밖으로 나가 앞 결과까지 0건
    ),
    KstartupCollector: Case(
        lambda: KstartupCollector(api_key="k"), _mock_kstartup,
        lambda i: {"pbanc_sn": i, "biz_pbanc_nm": f"공고{i}", "pbanc_rcpt_bgng_dt": TODAY.strftime("%Y%m%d"),
                   "rcrt_prgs_yn": "Y"},
        "pbanc_sn", "biz_pbanc_nm", 0, "pbanc_rcpt_end_dt",
        bad_format={"biz_pbanc_nm": ["제목이 list"]},
    ),
    Subsidy24Collector: Case(
        lambda: Subsidy24Collector(api_key="k"), _mock_subsidy24,
        lambda i: {"서비스ID": i, "서비스명": f"서비스{i}", "소관기관명": "기관"},
        "서비스ID", "서비스명", 0,
        "소관기관명",  # v1.2.4까지 null이면 ValidationError로 건너뛰었다 — 선택 필드다
        bad_format={"서비스명": ["제목이 list"]},
    ),
    SmesCollector: Case(
        lambda: SmesCollector(api_key="k"), _mock_smes,
        lambda i: {"itemId": i, "title": f"공고{i}", "viewUrl": "https://example.com"},
        "itemId", "title", "0", "viewUrl",
        # XML은 값이 전부 문자열이라 "형식 이상"을 만들 수 없다(태그 누락은 위 필수 필드 케이스가 본다) → bad_format 없음
    ),
    NaraCollector: Case(
        lambda: NaraCollector(api_key="k"), _mock_nara,
        lambda i: {"bidNtceNo": f"R{i}" if i != "0" else "0", "bidNtceOrd": "000", "bidNtceNm": f"공고{i}",
                   "ntceInsttNm": "기관"},
        "bidNtceNo", "bidNtceNm", "0", "bidClseDt",
        collect_kwargs={"bid_types": ["용역"]},
        bad_format={"asignBdgtAmt": "미정"},  # 종전엔 로그만 남기고 조용히 버렸다
    ),
    AlioCollector: Case(
        AlioCollector, _mock_alio,
        lambda i: {"seq": i, "rtitle": f"공고{i}", "pname": "기관", "bdate": TODAY.strftime("%Y.%m.%d")},
        "seq", "rtitle", 0, "bidInfoEndDt",
        bad_format={"bdate": "날짜아님"},
    ),
    LhCollector: Case(
        lambda: LhCollector(api_key="k"), _mock_lh,
        lambda i: {"bidNum": i, "bidnmKor": f"공고{i}", "tndrbidRegDt": TODAY.strftime("%Y%m%d")},
        "bidNum", "bidnmKor", "0", "tndrdocAcptEndDtm",
        bad_format={"tndrbidRegDt": "날짜아님"},
    ),
    KogasCollector: Case(
        lambda: KogasCollector(api_key="k"), _mock_kogas,
        lambda i: {"NOTICE_CODE": i, "NOTICE_NAME": f"공고{i}", "NOTICE_DT": TODAY.strftime("%Y-%m-%d")},
        "NOTICE_CODE", "NOTICE_NAME", "0", "END_DT",
        bad_format={"NOTICE_DT": "날짜아님"},
    ),
    D2bCollector: Case(  # 목록 5종 중 국내경쟁에만 항목을 싣는다(_mock_d2b)
        lambda: D2bCollector(api_key="k"), _mock_d2b,
        lambda i: {"g2bPblancNo": i, "pblancOdr": "1", "bidNm": f"공고{i}", "pblancDate": TODAY.strftime("%Y%m%d"),
                   # 상세 화면 주소 필드(v1.6.0) — 없으면 "주소 불가" errors가 붙는다(test_d2b::test_unbuildable_url_is_reported)
                   "orntCode": "ERA", "pblancSeCode": "B", "pblancNo": "ERA0005", "dcsNo": "5606N", "demandYear": "2026"},
        "g2bPblancNo", "bidNm", "0", "biddocPresentnClosDt",
        # XML은 값이 전부 문자열이고 날짜는 선택 필드라 "형식 이상"을 만들 수 없다 → bad_format 없음
    ),
    KwaterCollector: Case(  # 오퍼레이션 4종 중 용역에만 항목을 싣는다(_mock_kwater)
        lambda: KwaterCollector(api_key="k"), _mock_kwater,
        lambda i: {"tndrPbanno": i, "tndrPblancNm": f"공고{i}", "tndrPblancDe": int(TODAY.strftime("%Y%m%d"))},
        "tndrPbanno", "tndrPblancNm", 0, "tndrPblancEnddt",
        bad_format={"tndrPblancDe": "날짜아님"},
    ),
}

# HTML 행 단위라 "항목 필드"가 없다 — 행 예외·셀렉터 불일치는 test_generic_scraper가 따로 본다. 원문 전부(원칙 ①)도 별도 설계(plan.md 보류)
EXEMPT = {"GenericScraper"}

PARAMS = [pytest.param(c, id=c.__name__) for c in CASES]


def test_every_collector_has_a_case():
    """새 수집기를 __all__에 넣으면 여기서 실패한다 — CASES에 추가하거나 EXEMPT에 이유와 함께 넣을 것."""
    exported = {
        obj for name in bid_collectors.__all__
        if inspect.isclass(obj := getattr(bid_collectors, name))
        and issubclass(obj, BaseCollector) and obj is not BaseCollector
    }
    covered = set(CASES) | {c for c in exported if c.__name__ in EXEMPT}
    assert exported == covered


async def _collect(case: Case, items: list[dict]):
    with respx.mock:
        case.mock(items)
        return await case.collector().collect(days=1, **case.collect_kwargs)


@pytest.mark.parametrize("cls", PARAMS)
async def test_null_title_skips_only_that_item(cls):
    case = CASES[cls]
    bad = case.item(2)
    bad[case.title_field] = None
    result = await _collect(case, [case.item(1), bad])

    assert len(result.notices) == 1
    assert result.is_partial is True
    assert len(result.errors) == 1
    assert "1건 건너뜀" in result.errors[0]
    assert f"필수 필드 없음: {case.title_field} 1건" in result.errors[0]


@pytest.mark.parametrize("cls", [p for p in PARAMS if CASES[p.values[0]].bad_format])
async def test_bad_format_skips_only_that_item(cls):
    case = CASES[cls]
    bad = {**case.item(2), **case.bad_format}
    result = await _collect(case, [case.item(1), bad])

    assert len(result.notices) == 1
    assert result.is_partial is True
    assert len(result.errors) == 1
    assert "1건 건너뜀" in result.errors[0]


@pytest.mark.parametrize("cls", PARAMS)
async def test_missing_id_items_are_not_merged(cls):
    """ID 없는 3건 → `{접두사}-` 1건으로 합쳐지지 않고 0건 + 사유 한 줄에 건수 3."""
    case = CASES[cls]
    items = []
    for i in range(3):
        it = case.item(i + 1)
        del it[case.id_field]
        items.append(it)
    result = await _collect(case, items)

    assert result.total_fetched == 0
    assert result.errors == [
        f"항목 파싱 예외로 3건 건너뜀 — 필수 필드 없음: {case.id_field} 3건 (응답 형식 변경 의심)"
    ]


@pytest.mark.parametrize("cls", PARAMS)
async def test_zero_id_is_valid(cls):
    case = CASES[cls]
    result = await _collect(case, [case.item(case.zero_id)])

    assert result.errors == []
    assert len(result.notices) == 1
    assert "0" in result.notices[0].bid_no.split("-")


@pytest.mark.parametrize("cls", PARAMS)
async def test_null_optional_field_keeps_item(cls):
    """(v1.2.5 B) 선택 필드가 null인 항목은 건너뛰지 않고 그 필드만 비운 채 돌아온다 — 2건 중 1건 null → 2건, errors 없음."""
    case = CASES[cls]
    second = case.item(2)
    second[case.optional_field] = None
    result = await _collect(case, [case.item(1), second])

    assert result.errors == []
    assert len(result.notices) == 2


# 응답에 있을 리 없는 이름 — "모르는 필드도 전부"를 잰다. 빈 값 3종은 빠져야 하고 0은 값이다
EXTRA_PROBE = {"zz_unknown": "값", "zz_zero": 0, "zz_empty": "", "zz_blank": "  ", "zz_none": None}


def _nonempty_keys(item: dict) -> set[str]:
    """테스트 쪽의 독립 구현 — 헬퍼(raw_fields)를 쓰지 않고 같은 규칙으로 다시 센다(XML은 값이 전부 문자열이 된다)."""
    return {k for k, v in item.items() if v is not None and str(v).strip() != ""}


@pytest.mark.parametrize("cls", PARAMS)
async def test_extra_has_every_nonempty_field(cls):
    """(v1.2.5 원칙 ①) 모르는 필드까지 전부, 빈 값만 빼고 원래 이름으로 extra에 담긴다."""
    case = CASES[cls]
    item = {**case.item(1), **EXTRA_PROBE}
    result = await _collect(case, [item])

    assert len(result.notices) == 1
    extra = result.notices[0].extra
    assert set(extra) == _nonempty_keys(item)
    assert extra["zz_unknown"] == "값"
    assert extra["zz_zero"] in (0, "0")  # XML은 텍스트 "0"
    assert not {"zz_empty", "zz_blank", "zz_none"} & set(extra)


@pytest.mark.parametrize("cls", PARAMS)
async def test_extra_keys_are_original_names(cls):
    """(v1.2.5 원칙 ①) 이름을 바꾼 키(영어 별칭·요청 문맥)가 없다 — extra의 키는 전부 응답 항목의 키다."""
    case = CASES[cls]
    item = case.item(1)
    result = await _collect(case, [item])

    extra = result.notices[0].extra
    assert set(extra) <= set(item)
    assert not {"bid_type", "data_type", "contact", "est_price"} & set(extra)
