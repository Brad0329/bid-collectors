"""LH(한국토지주택공사) 입찰공고 수집기 (F-011, v1.4.0).

API: GET https://apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo (data.go.kr 15159012)
인증: serviceKey (DATA_GO_KR_KEY) — 활용신청 필요
응답: XML, **EUC-KR 선언** — 바이트 그대로 파싱한다(utils/datagokr.py)
날짜: tndrbidRegDtStart/End(YYYYMMDD) = 공고일(tndrbidRegDt) 범위. 빈 결과는 resultCode 03 NODATA.
실측(2026-09-25, docs/institution_sources.md): numOfRows 상한 없음(5000 요청에 3,791건), 14일 139건·하루 최대 38건.
정정·취소 공고는 새 행이 아니라 **같은 행의 bidDegree(00→01)와 공고일이 정정일로 바뀐다** — bid_no에 차수를 넣지 않는다.
"""

import re
import ssl
import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlencode

import certifi  # httpx의 필수 의존성(httpx가 쓰는 신뢰 목록) — 새 설치가 아니다
from bs4 import BeautifulSoup

from .base import BaseCollector, raw_fields, require_fields
from .models import Notice
from .utils.datagokr import fetch_pages, parse_xml
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

API_URL = "https://apis.data.go.kr/B552555/OpenBidInfoList/getOpenBidInfo"
ROWS = 1000
DEFAULT_MAX_PAGES = 20
NODATA = ("03",)
ORGANIZATION = "한국토지주택공사"  # 단일 기관 API라 응답에 기관 필드가 없다 — 알리오 pname과 같은 이름(지역본부는 extra의 zoneHqCd)

# LH 전자입찰 상세 화면 — 업무 구분(cstrtnJobGbNm)마다 경로가 다르다. 3종은 알리오 refrUrl 17건(2026-09-25),
# 물품은 검색 화면 JS의 업무 코드 30 경로로 확인(2026-09-26 표본 5건 + 실측 1건 열림).
_DETAIL_BASE = "https://ebid.lh.or.kr/ebid.et.tp.cmd."
_DETAIL_CMD = {
    "시설공사": "BidConstructDetailListCmd",
    "용역": "BidsrvcsDetailListCmd",
    "지급자재": "BidctrctgdsDetailListCmd",
    "물품": "BidgdsDetailListCmd",
}
# 모르는 업무 구분 — 추측한 경로로 깨진 링크를 주지 않고 전자입찰 첫 화면으로
LIST_URL = "https://ebid.lh.or.kr/"

# ── 상세(v1.5.0) — 공식 API가 아니라 전자입찰 사이트 화면이다. 사이트 개편 시 깨지며 그때는 예외로 드러난다.
# bid_no엔 업무 구분·차수가 없고, 상세 화면은 둘 다 맞아야 한다(차수를 빼면 빈 틀, 00이면 원공고, 틀린 업무 경로도 200에 다른 화면 틀 —
# 2026-09-26 실측). → 검색 화면을 공고번호로 1회 불러 최신 차수·업무 코드를 얻고 상세 1회(총 2회).
SITE = "https://ebid.lh.or.kr/"
SEARCH_URL = SITE + "ebid.et.tp.cmd.BidMasterListCmd.dev"
DOWNLOAD_URL = SITE + "ebid.framework.download.dev"
# 검색 결과 행의 onclick = fn_dds_open(공고번호, 차수, 업무 코드, 긴급 여부) — 검색 화면 JS의 업무 코드 → 상세 경로(물품 30 포함)
_SEARCH_ROW = re.compile(r"fn_dds_open\('(\d+)',\s*'(\d+)',\s*'(\d+)',\s*'\w'\)")
_JOB_CMD = {"10": "BidConstructDetailListCmd", "20": "BidsrvcsDetailListCmd",
            "30": "BidgdsDetailListCmd", "40": "BidctrctgdsDetailListCmd"}
# 상세 화면 첨부 = fn_dds_open(문서 종류, 저장 파일명, 서버 경로, 원래 파일명) → 사이트 JS cn_downLoadFile의 내려받기 요청(ebid_common.js)
_ATTACH = re.compile(r"fn_dds_open\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
_SELF_CLOSING_TR = re.compile(r"(<tr\b[^>]*?)\s*/>")  # 공고변경정보 행이 <tr .../>로 닫혀 있어 칸이 표 밖으로 떨어진다

# ebid.lh.or.kr은 중간 인증서를 보내지 않아 certifi만으로는 검증에 실패한다(브라우저·Windows는 AIA로 받아 온다, 2026-09-26 실측).
# 발급자 인증서의 caIssuers(http://public.wisekey.com/crt/tsrsasecureca2.cer)에서 받은 중간 인증서를 더해 검증한다 — 검증은 끄지 않는다.
# 만료 2030-05-26. 사이트가 다른 CA로 인증서를 바꾸면 검증 실패 예외로 드러난다(2026-09-26 사용자: 동봉 결정).
LH_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
MIIFjjCCBHagAwIBAgIQcCosoce0HIWpncOmISmyLzANBgkqhkiG9w0BAQsFADBt
MQswCQYDVQQGEwJDSDEQMA4GA1UEChMHV0lTZUtleTEiMCAGA1UECxMZT0lTVEUg
Rm91bmRhdGlvbiBFbmRvcnNlZDEoMCYGA1UEAxMfT0lTVEUgV0lTZUtleSBHbG9i
YWwgUm9vdCBHQiBDQTAeFw0yNTA1MjcxNTEwMzRaFw0zMDA1MjYxNTEwMzRaMFEx
CzAJBgNVBAYTAkNIMR0wGwYDVQQKDBRUdXJpbmdTaWduIEdsb2JhbCBTQTEjMCEG
A1UEAwwaVHVyaW5nU2lnbiBSU0EgU2VjdXJlIENBIDIwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQDGDBcFU6l+Hs5OUzBVjDQP8xGhdPG7xvNPu2Q5FF1f
L4IOIIYnx2E3ZFVbYf4a6d/8q4HFlWLT98BIPGo3nlsZiyaKb6MKMGONE5/4DfMk
zn+JkQaggOmXNLhn0hbezFJOJaYBcCroBZmDyOKbHRSHnBDZuG8Fx5UqbSG3Zlic
ywd4ET0CZXL/QZCcJzRJ6OMyndQpvmxbCq8TUwbqT4FwFDOwigqBPNlEgjSje0vc
3Xg7KUOgcHs9NI26Vo72YR/uiA9N/0gMfum0DLp/31vhIHw68LC/7cU/4Rp6yYaY
c8OfyhRuwfsMHWTXpAroHqbK8zlK4ZFOaTv+6MeFHnADyYRLdLl4cPTDmLUZFbyo
3Ec/NFepKYP/hFM0Fo7wFHMg1QsLSOD9KcQzxOkAhggX5bHd3DvQZyo3g3EnC6l0
FFQ4UwTI2qLKXpVN8EUfh3HSJmbVsQoyUdmbOz+qjtIjHAP2mIwip6AvE3DWA28E
K09fLTCbCbP/NBAfZAWbfzSeombpwib5pLUQ6/0FzMRw8dE6jm5t5L5INBXaUUCx
wXM9BJxMc+gqjxRJD5SEbyK0dFR74n2nkzzUS83GyFJXkfYDOnYBUN0kGtUzn4bt
RLdQ00+xewgFVMPGXTeQMK0VpavOb0uFcu4ZhLA28B2iT8XWc4Not1Bj84+5O50K
EwIDAQABo4IBRDCCAUAwEgYDVR0TAQH/BAgwBgEB/wIBADAfBgNVHSMEGDAWgBQ1
D8g2Y17io+z5O2YVzlFS45GaPTBrBggrBgEFBQcBAQRfMF0wNgYIKwYBBQUHMAKG
Kmh0dHA6Ly9wdWJsaWMud2lzZWtleS5jb20vY3J0L293Z3JnYmNhLmNlcjAjBggr
BgEFBQcwAYYXaHR0cDovL29jc3Aud2lzZWtleS5jb20wEQYDVR0gBAowCDAGBgRV
HSAAMB0GA1UdJQQWMBQGCCsGAQUFBwMCBggrBgEFBQcDATA7BgNVHR8ENDAyMDCg
LqAshipodHRwOi8vcHVibGljLndpc2VrZXkuY29tL2NybC9vd2dyZ2JjYS5jcmww
HQYDVR0OBBYEFM3OdTxWi2FRu9+xUPmb6hymFzMRMA4GA1UdDwEB/wQEAwIBBjAN
BgkqhkiG9w0BAQsFAAOCAQEAbjvOB6/tTaX0YG/8sPytIvU6nEWuq2Zfxl7FMMB7
wAm7IPPf5MSTXcc8mmPh97YDj/A6N3jOf09G7IJEGYo7Sf9948ZhL6czKmByyKhU
r3yCEmVV/+MyhTvhc5aJIG6dnADXw8C1lMwEt6gzMolsNyQ3gY6slPxZ2xUEcPZi
wm9veB9aR+QfcUl7UHQHpfC7EoeelSir7AfcvLdbseaqM5GeWlFWmsCH7SweFybv
Tjz94Rfsafz5fEL2EaApecOUK3bLh9mO6cgL7n8yryrUKG5hY6D4OirSYpYJvS6y
u2wLYijDNYa2wMqRFdIoMB/7NxDyVQ3lfc7Kj50d33TUsQ==
-----END CERTIFICATE-----
"""


def lh_ssl_context() -> ssl.SSLContext:
    """certifi 신뢰 목록 + LH 중간 인증서. 검증(호스트 이름·체인)은 기본값 그대로 켜져 있다."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cadata=LH_INTERMEDIATE_PEM)
    return ctx


class LhCollector(BaseCollector):
    """LH 입찰공고 수집기."""

    source_name = "LH"

    async def _fetch(self, days: int = 1, **kwargs) -> tuple[list[Notice], int, list[str]]:
        end = datetime.now()
        start = end - timedelta(days=days)
        params = {
            "serviceKey": self.api_key,
            "tndrbidRegDtStart": start.strftime("%Y%m%d"),
            "tndrbidRegDtEnd": end.strftime("%Y%m%d"),
        }
        async with create_client(timeout=30.0) as client:
            items, pages, errors = await fetch_pages(
                client, API_URL, params, lambda c: parse_xml(c, NODATA),
                rows=ROWS, max_pages=kwargs.get("max_pages", DEFAULT_MAX_PAGES),
                label="[LH]", mask=self._mask,
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
        """공고 1건 상세 (v1.5.0) — 공고부서·입찰방식·재입찰·현장설명·참가지역·첨부 등. 금액은 목록 extra에도 있다.

        호출 2회(검색 1 + 상세 1). 수집 경로에서는 부르지 않는다.
        Returns: 키 `"표 이름/항목명"`(참가지역1~4가 두 표에 겹쳐 항목명만으로는 충돌 — 23/23건) → 값(공백 정리한 원문, 빈 값 제외),
            제목 행이 전부 th인 표(요구면허·파일정보·공고변경정보)는 `표 이름` → list[dict](열 제목이 키). 같은 이름의 표가 다시 나오면
            `표 이름#2`(시설공사 요구면허가 2개 — 열 구성이 다르다). + `attachments`(파일정보의 내려받기 요청, url 조립은 사이트 JS와 같게)
            + `content`("" — 본문 필드 없음, 공고문은 첨부 hwp).
        Raises: 검색 결과 없음(없는 공고), 공고번호 칸이 `{번호} - {차수}`가 아니거나 건명 빈 값(화면 개편·차수 불일치),
            첨부 링크를 다 읽지 못함, HTTP 오류·TLS 검증 실패 → 예외.
        """
        bid_num = bid_no.removeprefix("LH-") if bid_no.startswith("LH-") else ""
        if not bid_num.isdigit():
            raise ValueError(f"LH bid_no 형식이 아님(LH-{{bidNum}}): {bid_no!r}")

        async with create_client(timeout=20.0, verify=lh_ssl_context()) as client:
            # 날짜 칸을 비워 보내야 한다 — 빼면 서버 기본값(마감 오늘~+5개월)이 걸려 지난 공고가 0건(실측)
            resp = await client.post(SEARCH_URL, data={"s_bidNum": bid_num, "s_tndrdocAcptOpenDtm": "",
                                                       "s_tndrdocAcptEndDtm": ""})
            resp.raise_for_status()
            degree, cmd = _latest_degree_and_cmd(resp.text, bid_num, bid_no)
            resp = await client.get(f"{SITE}ebid.et.tp.cmd.{cmd}.dev", params={"bidNum": bid_num, "bidDegree": degree})
            resp.raise_for_status()
        return _parse_detail(resp.text, bid_num, degree, bid_no)

    async def health_check(self) -> dict:
        start = time.time()
        try:
            today = datetime.now()
            async with create_client(timeout=15.0) as client:
                resp = await client.get(API_URL, params={
                    "serviceKey": self.api_key, "pageNo": "1", "numOfRows": "1",
                    "tndrbidRegDtStart": (today - timedelta(days=7)).strftime("%Y%m%d"),
                    "tndrbidRegDtEnd": today.strftime("%Y%m%d"),
                })
                resp.raise_for_status()
                parse_xml(resp.content, NODATA)
            return {"status": "ok", "source": self.source_name,
                    "response_time_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "source": self.source_name, "message": self._mask(str(e)),
                    "response_time_ms": int((time.time() - start) * 1000)}


def _latest_degree_and_cmd(html: str, bid_num: str, bid_no: str) -> tuple[str, str]:
    """검색 결과 → (최신 차수, 상세 화면 경로). 번호가 정확히 같은 행만 본다(검색은 완전 일치 — 실측)."""
    hits = {(deg, code) for num, deg, code in _SEARCH_ROW.findall(html) if num == bid_num}
    if not hits:
        raise ValueError(f"LH 상세 {bid_no}: 검색 결과 없음 — 없는 공고번호이거나 검색 화면 개편")
    degree, code = max(hits)  # 표본은 늘 1행 — 여러 차수가 나오면 가장 높은 차수
    if len({c for _, c in hits}) > 1 or code not in _JOB_CMD:
        raise ValueError(f"LH 상세 {bid_no}: 업무 코드 판정 불가 {sorted(hits)} — 검색 화면 개편 의심")
    return degree, _JOB_CMD[code]


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).replace("\xa0", " ").split())


def _parse_detail(html: str, bid_num: str, degree: str, bid_no: str) -> dict:
    """상세 HTML → fetch_detail 반환 dict. 표마다 `summary` 속성이 이름이다(2026-09-26 표본 23건, 업무 4종)."""
    soup = BeautifulSoup(_SELF_CLOSING_TR.sub(r"\1>", html), "lxml")
    detail: dict = {}
    seen: Counter[str] = Counter()
    files_html = ""
    for table in soup.find_all("table", summary=True):
        name = " ".join(table["summary"].split())
        seen[name] += 1
        key_name = name if seen[name] == 1 else f"{name}#{seen[name]}"
        rows = table.find_all("tr")
        head = rows[0].find_all(["th", "td"], recursive=False) if rows else []
        if head and all(c.name == "th" for c in head):  # 목록형 표 — 제목 행 + 데이터 행
            headers = [_text(c) for c in head]
            grid = []
            for tr in rows[1:]:
                cells = []
                for c in tr.find_all(["th", "td"], recursive=False):
                    cells += [_text(c)] + [""] * (int(c.get("colspan") or 1) - 1)
                if len(cells) != len(headers):
                    raise ValueError(f"LH 상세 {bid_no}: '{name}' 표 열 수 불일치(제목 {len(headers)}·행 {len(cells)}) — 화면 개편 의심")
                if row := {h: v for h, v in zip(headers, cells) if v}:
                    grid.append(row)
            if grid:
                detail[key_name] = grid
            if name == "파일정보":
                files_html += str(table)
            continue
        for tr in rows:  # 항목형 표 — th 바로 뒤의 td가 값
            cells = tr.find_all(["th", "td"], recursive=False)
            for th, td in zip(cells, cells[1:]):
                if th.name != "th" or td.name != "td" or not (value := _text(td)):
                    continue
                key = f"{key_name}/{_text(th)}"
                if key in detail:  # 표본엔 없었다 — 버리지 않고 모은다
                    prev = detail[key]
                    detail[key] = (prev if isinstance(prev, list) else [prev]) + [value]
                else:
                    detail[key] = value

    number = detail.get("공고일반정보/입찰공고번호")
    if number != f"{bid_num} - {degree}" or not detail.get("공고일반정보/입찰공고건명"):
        # 없는 번호·틀린 차수도 200에 같은 화면 틀(번호 칸 "-", 건명 빈 값)이 온다 — 빈 dict로 넘기지 않는다
        raise ValueError(f"LH 상세 {bid_no}: 공고번호 칸={number!r}(기대 '{bid_num} - {degree}')·건명 "
                         f"{'있음' if detail.get('공고일반정보/입찰공고건명') else '없음'} — 화면 개편 또는 차수 불일치")
    links = _ATTACH.findall(files_html)
    if len(links) != files_html.count("fn_dds_open("):
        raise ValueError(f"LH 상세 {bid_no}: 첨부 링크 {files_html.count('fn_dds_open(')}개 중 {len(links)}개만 읽음 — 화면 개편 의심")
    detail["content"] = ""
    detail["attachments"] = [
        {"name": original,
         # 사이트 JS와 같게: 원래 파일명의 공백을 지운다("공백이 들어가면 다운로드 받지 못하기 때문" — ebid_common.js 주석)
         "url": f"{DOWNLOAD_URL}?{urlencode({'download.filespec': 'bidinfo', 'download.filename': original.replace(' ', ''), 'download.savedname': saved})}"}
        for _doc_type, saved, _path, original in links
    ]
    return detail


def detail_url(job_type: str, bid_num: str, degree: str) -> str:
    cmd = _DETAIL_CMD.get(job_type)
    if not cmd:
        return LIST_URL
    return f"{_DETAIL_BASE}{cmd}.dev?bidNum={bid_num}&bidDegree={degree or '00'}"


def _item_to_notice(item) -> Notice:
    def t(tag: str) -> str:
        return (item.findtext(tag) or "").strip()

    bid_num = t("bidNum")
    title = " ".join(t("bidnmKor").split())
    start_str = parse_date(t("tndrbidRegDt"))
    require_fields(bidNum=bid_num, bidnmKor=title, tndrbidRegDt=start_str)
    end_str = parse_date(t("tndrdocAcptEndDtm"))  # 입찰서 접수 마감 "2026/09/21 10:00"
    job_type = t("cstrtnJobGbNm")
    url = detail_url(job_type, bid_num, t("bidDegree"))
    return Notice(
        source="LH",
        bid_no=f"LH-{bid_num}",
        title=title,
        organization=ORGANIZATION,
        start_date=start_str,
        end_date=end_str or None,
        status=determine_status(end_str) if end_str else "ongoing",
        url=url,
        detail_url=url,
        budget=None,  # 추정가격·설계가·기초금액 중 무엇을 budget으로 볼지는 원칙 ② 결정 — 금액은 extra 원문(2026-09-26 사용자)
        category=job_type,
        extra=raw_fields(item),
    )
