"""data.go.kr 기관 API 공통 — XML 응답 파싱과 페이지 루프 (v1.4.0, 자체조달 기관 수집기 4종이 공유).

실측 함정(2026-09-25, docs/institution_sources.md):
- 200이 성공이 아니다 — 에러는 본문의 `resultCode`(게이트웨이 에러는 `cmmMsgHeader/returnReasonCode`)에 온다.
- 수자원은 약 51KB를 넘는 응답에 HTTP 200 + 본문 0바이트를 준다 — 에러 코드도 건수도 없어 "0건"으로 삼키면 조용한 실패다.
- LH는 XML 선언이 EUC-KR이다 — 문자열로 디코드해 다시 넣지 말고 바이트 그대로 lxml에 넘긴다(선언을 lxml이 읽는다).

nara·smes의 XML 파싱은 이 모듈 이전 코드라 따로 있다(CLAUDE.md '같은 규칙의 복제').
"""

import logging
from typing import Callable

import httpx
from lxml import etree

logger = logging.getLogger("bid_collectors")

Parser = Callable[[bytes], tuple[list, int]]

# 빈 본문 오류 문구 — 수집기가 "한 페이지로 받기엔 크다"를 알아보고 나눠 받는 데 쓴다(kwater)
EMPTY_BODY_MSG = "빈 응답 본문(HTTP 200)"


def parse_xml(content: bytes, nodata_codes: tuple[str, ...] = ()) -> tuple[list[etree._Element], int]:
    """XML 응답 → (item 목록, totalCount). 실패는 ValueError.

    Args:
        nodata_codes: "결과 없음"을 에러 코드로 주는 API의 그 코드(LH `03 NODATA`) — 0건으로 돌려준다.
    """
    body = content.strip()
    if not body:
        raise ValueError(f"{EMPTY_BODY_MSG} — 응답 크기 초과 등 서버 오류 의심")
    root = etree.fromstring(body)

    code = root.findtext(".//resultCode")
    if code is None:
        # 게이트웨이 에러(키 미등록·트래픽 초과 등)는 OpenAPI_ServiceResponse/cmmMsgHeader 모양으로 온다
        reason = root.findtext(".//returnReasonCode")
        msg = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
        raise ValueError(f"API 에러: returnReasonCode={reason} - {msg}" if reason or msg
                         else f"응답 형식 이상 — resultCode 없음(루트 <{root.tag}>)")
    code = code.strip()
    if code in nodata_codes:
        return [], 0
    if code != "00":
        raise ValueError(f"API 에러: {code} - {(root.findtext('.//resultMsg') or '').strip()}")
    return root.findall(".//item"), int((root.findtext(".//totalCount") or "0").strip() or 0)


async def fetch_pages(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    parse: Parser,
    *,
    rows: int,
    max_pages: int,
    label: str,
    mask: Callable[[str], str],
) -> tuple[list, int, list[str]]:
    """totalCount까지 페이지를 넘겨 항목을 모은다 → (항목, 처리한 페이지 수, errors).

    페이지 실패는 앞 페이지 결과를 남기고 errors에 원인을 담는다(v1.1 계약). max_pages에서 멈추면
    잘랐다는 사실과 전체 건수를 errors에 싣는다(CLAUDE.md '조용한 절단 금지').
    """
    items: list = []
    errors: list[str] = []
    pages = 0
    total = 0
    page = 1
    while page <= max_pages:
        try:
            resp = await client.get(url, params={**params, "pageNo": str(page), "numOfRows": str(rows)})
            resp.raise_for_status()
            page_items, total = parse(resp.content)
        except Exception as e:
            msg = mask(f"{label} 페이지 {page} 요청 실패: {type(e).__name__}: {e}")
            logger.error(msg)
            errors.append(msg)
            break
        if not page_items:
            if len(items) < total:
                # totalCount가 남았는데 빈 페이지 — 범위 밖 페이지도 00 + 빈 items로 오는 API라(수자원) 조용히 멈추면 절단이다
                msg = f"{label} 페이지 {page}가 비었음 — 전체 {total}건 중 {len(items)}건만 받음"
                logger.warning(msg)
                errors.append(msg)
            break
        pages += 1
        items.extend(page_items)
        if page * rows >= total:
            break
        page += 1
    else:
        msg = f"{label} max_pages={max_pages} 상한 도달로 중단 — 전체 {total}건 중 {max_pages * rows}건까지만 조회"
        logger.warning(msg)
        errors.append(msg)
    return items, pages, errors
