"""Config 기반 범용 HTML 스크래퍼 엔진.

AI가 생성하거나 수동 작성한 config JSON을 받아 임의의 HTML 게시판을 파싱한다.

사용법:
    config = {"name": "한국콘텐츠진흥원", "source_key": "kocca", ...}
    scraper = GenericScraper(config)
    result = await scraper.collect(days=30)
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable, Literal, NamedTuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseCollector
from .models import Notice
from .utils.dates import parse_date
from .utils.http import create_client
from .utils.status import determine_status

logger = logging.getLogger("bid_collectors")


class _RowScan(NamedTuple):
    """한 페이지 목록 행의 파싱 결과."""

    notices: list[Notice]
    has_old: bool   # 기준일 이전 행이 있었다
    skipped: int    # 파싱 예외로 건너뛴 행
    rows: int       # 목록 셀렉터에 잡힌 행
    no_title: int   # 제목 셀렉터로 제목을 못 얻은 행
    no_date: int    # 날짜가 없어 skip_no_date로 건너뛴 행


# ─────────────────────────────────────────────
# ScraperConfig 모델
# ─────────────────────────────────────────────


class ScraperConfig(BaseModel):
    """Config 기반 HTML 스크래퍼 설정.

    AI가 생성하거나 수동으로 작성한 config를 검증하는 스키마.
    model_json_schema()로 AI 프롬프트에 사용할 JSON Schema 추출 가능.
    """

    # ──── 필수 필드 ────
    name: str = Field(..., min_length=1,
        description="사이트/기관 표시명. source 필드 + organization으로 사용")
    source_key: str = Field(..., min_length=1, max_length=30, pattern=r'^[a-z0-9_]+$',
        description="영문 소문자 키. bid_no 생성용 (SCR-{source_key}-{hash})")
    list_url: str = Field(...,
        description="게시판 목록 페이지 URL")
    list_selector: str = Field(...,
        description="행(row) CSS 셀렉터. 예: 'table tbody tr', 'div.item'")
    title_selector: str = Field(...,
        description="행 내 제목 요소 CSS 셀렉터. 예: 'td:nth-child(2) a'")
    date_selector: str = Field(...,
        description="행 내 날짜 요소 CSS 셀렉터. 예: 'td:nth-child(5)'")

    # ──── 공통 선택 필드 ────
    link_attr: str = Field(default="href",
        description="링크를 가져올 HTML 속성. 기본 href")
    link_base: str = Field(default="",
        description="상대 URL 변환용 base URL. 비어있으면 list_url 사용")
    pagination: str = Field(default="",
        description="페이지네이션 URL 접미사. {page} 또는 {offset} 플레이스홀더 사용. "
                    "예: '&page={page}', '&pager.offset={offset}'. "
                    "빈 문자열이면 페이지네이션 없음 (1페이지만)")
    max_pages: int = Field(default=3, ge=1, le=50,
        description="최대 수집 페이지 수")
    encoding: str = Field(default="utf-8",
        description="응답 인코딩")
    parser: Literal["html.parser", "lxml", "html5lib"] = Field(default="html.parser",
        description="BeautifulSoup 파서")

    # ──── Offset 기반 페이지네이션 ────
    offset_size: int = Field(default=0, ge=0,
        description="offset 기반 페이지네이션 시 페이지당 항목 수. "
                    "pagination에 {offset}이 있을 때만 사용")

    # ──── JavaScript 링크 추출 ────
    link_js_regex: str = Field(default="",
        description="JS onclick/href에서 ID 추출 정규식. "
                    r"예: fncShow\('(\d+)'\)")
    link_template: str = Field(default="",
        description="추출된 ID로 URL 생성 템플릿. "
                    "{id} = 첫 번째 그룹, {1}/{2}/{3} = 번호별 그룹. "
                    "예: '/detail.do?seq={id}'")

    # ──── 세션/쿠키 ────
    session_init_url: str = Field(default="",
        description="쿠키 획득용 사전 요청 URL. 세션 인증이 필요한 사이트에 사용")

    # ──── POST 요청 ────
    post_data: dict | None = Field(default=None,
        description="POST 요청 데이터. None이면 GET 사용. "
                    "빈 dict({})도 POST 트리거")
    post_json: bool = Field(default=False,
        description="True면 JSON body, False면 form-encoded")
    page_param_key: str = Field(default="",
        description="post_data 내 페이지 번호 키. 예: 'pageIndex'")

    # ──── 컨테이너 격리 ────
    grid_selector: str = Field(default="",
        description="데이터 영역 CSS 셀렉터. 설정하면 이 영역 내에서만 list_selector 적용")

    # ──── 동작 플래그 ────
    skip_no_date: bool = Field(default=True,
        description="True면 날짜 파싱 실패 행을 건너뜀. False면 날짜 없이도 수집")
    verify_ssl: bool = Field(default=True,
        description="False면 SSL 인증서 검증 비활성화 (인증서 문제 사이트용)")

    # ──── 검증 규칙 ────

    @field_validator('source_key')
    @classmethod
    def source_key_ascii(cls, v: str) -> str:
        if not v.isascii():
            raise ValueError('source_key는 ASCII 문자만 허용')
        return v.lower()

    @model_validator(mode='after')
    def validate_js_link_pair(self) -> 'ScraperConfig':
        """link_js_regex와 link_template은 반드시 함께 사용."""
        if bool(self.link_js_regex) != bool(self.link_template):
            raise ValueError('link_js_regex와 link_template은 반드시 함께 설정해야 합니다')
        return self

    @model_validator(mode='after')
    def validate_post_pagination(self) -> 'ScraperConfig':
        """page_param_key는 post_data가 있을 때만 사용 가능."""
        if self.page_param_key and self.post_data is None:
            raise ValueError('page_param_key를 사용하려면 post_data가 필요합니다')
        return self

    @model_validator(mode='after')
    def validate_offset_pagination(self) -> 'ScraperConfig':
        """offset_size는 pagination에 {offset}이 있을 때만 유효."""
        if self.offset_size > 0 and '{offset}' not in self.pagination:
            raise ValueError('offset_size를 사용하려면 pagination에 {offset} 플레이스홀더가 필요합니다')
        return self


# ─────────────────────────────────────────────
# GenericScraper 클래스
# ─────────────────────────────────────────────


class GenericScraper(BaseCollector):
    source_name = "scraper"

    def __init__(
        self,
        config: ScraperConfig | dict,
        event_hooks: dict[str, list[Callable]] | None = None,
        **kwargs,
    ):
        """config 검증 및 초기화. API 키 불필요.

        Args:
            config: ScraperConfig 인스턴스 또는 raw dict (자동 검증)
            event_hooks: httpx event_hooks 형식({"request": [...], "response": [...]}).
                session_init_url·목록 페이지·health_check·리다이렉트로 따라가는 요청 전부에 걸린다.
                소비자의 SSRF 방어 훅을 꽂는 자리 — 훅이 예외를 던지면 그 요청은 실패로 errors에 기록된다.

        Raises:
            pydantic.ValidationError: config 검증 실패
        """
        if isinstance(config, dict):
            config = ScraperConfig(**config)
        self.config = config
        self.source_name = config.name
        self.api_key = None
        self.event_hooks = event_hooks

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"timeout": 15.0, "verify": self.config.verify_ssl}
        if self.event_hooks:
            kwargs["event_hooks"] = self.event_hooks
        return create_client(**kwargs)

    async def _fetch(self, days: int = 30, **kwargs) -> tuple[list[Notice], int, list[str]]:
        """설정된 사이트에서 공고 수집.

        Args:
            days: 수집 기간 (일). cutoff = now - timedelta(days)
            **kwargs:
                max_pages: int -- config의 max_pages 오버라이드
                delay: float -- 페이지 간 요청 간격(초). 기본 0.5

        Returns:
            (notices 리스트, 처리된 페이지 수, 부분 실패·절단 메시지)
        """
        max_pages = kwargs.get("max_pages", self.config.max_pages)
        if not self._is_paginated():
            # 페이지네이션이 없으면 같은 URL을 max_pages번 다시 받게 된다 — 1페이지가 전부다
            max_pages = 1
        delay = kwargs.get("delay", 0.5)
        cutoff = (datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

        all_notices: list[Notice] = []
        errors: list[str] = []
        pages_processed = 0
        skipped_rows = 0
        has_old = False

        logger.info(f"[{self.source_name}] 수집 시작: days={days}, max_pages={max_pages}")

        async with self._client() as client:
            # 세션 초기화 (쿠키 획득) — 실패해도 목록 요청은 시도하되 원인을 남긴다
            if self.config.session_init_url:
                try:
                    init_resp = await client.get(self.config.session_init_url)
                    init_resp.raise_for_status()
                except Exception as e:
                    msg = f"세션 초기화 요청 실패: {type(e).__name__}: {e}"
                    logger.warning(f"[{self.source_name}] {msg}")
                    errors.append(msg)

            for page in range(1, max_pages + 1):
                try:
                    resp = await self._fetch_page(client, page)
                except Exception as e:
                    # httpx 오류뿐 아니라 소비자 훅이 던진 예외(SSRF 차단 등)도 여기로 온다
                    msg = f"페이지 {page} 요청 실패: {type(e).__name__}: {e}"
                    logger.warning(f"[{self.source_name}] {msg}")
                    errors.append(msg)
                    break

                # 인코딩 처리
                if self.config.encoding.lower() != "utf-8":
                    text = resp.content.decode(self.config.encoding, errors="replace")
                else:
                    text = resp.text

                scan = self._parse_rows(text, cutoff)
                has_old = scan.has_old
                all_notices.extend(scan.notices)
                skipped_rows += scan.skipped
                pages_processed += 1

                # 종료 조건 — 기준일 이내 공고가 없는 페이지에서 멈춘다(임계 1, 2026-09-24 사용자 확정)
                if not scan.notices:
                    # 행은 잡혔는데 추출도 0건·기준일 이전 행도 0건이면 "오래된 페이지"가 아니라 셀렉터가 낡은 것이다
                    # (사이트 개편). 목록 행 자체가 0개면 빈 게시판과 구분할 수 없어 보고하지 않는다(2026-09-24 사용자 확정).
                    if scan.rows and not scan.has_old:
                        msg = (
                            f"페이지 {page}: 셀렉터 불일치 의심 — 목록 행 {scan.rows}개 중 추출 0건 "
                            f"(제목 없음 {scan.no_title}행, 날짜 없음 {scan.no_date}행, 파싱 예외 {scan.skipped}행)"
                        )
                        logger.warning(f"[{self.source_name}] {msg}")
                        errors.append(msg)
                    break

                # 요청 간격
                if page < max_pages:
                    await asyncio.sleep(delay)
            else:
                # 마지막 페이지까지 cutoff 이내 공고만 나왔다면 다음 페이지에도 있을 수 있다
                if self._is_paginated() and not has_old:
                    msg = (
                        f"max_pages={max_pages} 상한 도달로 중단 — {pages_processed}페이지 "
                        f"{len(all_notices)}건 수집, 다음 페이지 미확인(전체 건수 알 수 없음)"
                    )
                    logger.warning(f"[{self.source_name}] {msg}")
                    errors.append(msg)

        if skipped_rows:
            msg = f"행 파싱 예외로 {skipped_rows}행 건너뜀"
            logger.warning(f"[{self.source_name}] {msg}")
            errors.append(msg)

        logger.info(
            f"[{self.source_name}] 수집 완료: {len(all_notices)}건, {pages_processed}페이지"
        )
        return (all_notices, pages_processed, errors)

    async def health_check(self) -> dict:
        """1페이지 접근 테스트."""
        start = time.time()
        try:
            async with self._client() as client:
                resp = await self._fetch_page(client, 1)
                resp.raise_for_status()

            ms = int((time.time() - start) * 1000)
            return {"status": "ok", "source": self.source_name, "response_time_ms": ms}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {
                "status": "error",
                "source": self.source_name,
                "message": str(e),
                "response_time_ms": ms,
            }

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        page: int,
    ) -> httpx.Response:
        """단일 페이지 HTTP 요청. GET 또는 POST를 config에 따라 분기."""
        if self.config.post_data is not None:
            form = dict(self.config.post_data)
            if self.config.page_param_key:
                form[self.config.page_param_key] = page
            if self.config.post_json:
                resp = await client.post(self.config.list_url, json=form)
            else:
                resp = await client.post(self.config.list_url, data=form)
        else:
            url = self._build_page_url(page)
            resp = await client.get(url)

        resp.raise_for_status()
        return resp

    def _is_paginated(self) -> bool:
        """다음 페이지 요청이 실제로 달라지는가. POST는 page_param_key, GET은 pagination 접미사."""
        if self.config.post_data is not None:
            return bool(self.config.page_param_key)
        return bool(self.config.pagination)

    def _build_page_url(self, page: int) -> str:
        """페이지네이션 패턴에 따라 URL 구성."""
        if page == 1 or not self.config.pagination:
            return self.config.list_url

        pagination = self.config.pagination

        if self.config.offset_size and "{offset}" in pagination:
            offset = (page - 1) * self.config.offset_size
            return self.config.list_url + pagination.replace("{offset}", str(offset))

        return self.config.list_url + pagination.replace("{page}", str(page))

    def _parse_rows(self, html: str, cutoff: datetime) -> "_RowScan":
        """HTML을 파싱하여 Notice 리스트와 행 집계를 반환한다(집계는 셀렉터 불일치 판정용)."""
        soup = BeautifulSoup(html, self.config.parser)

        # grid_selector 적용
        if self.config.grid_selector:
            container = soup.select_one(self.config.grid_selector)
            if not container:
                return _RowScan([], False, 0, 0, 0, 0)
            rows = container.select(self.config.list_selector)
        else:
            rows = soup.select(self.config.list_selector)

        if not rows:
            return _RowScan([], False, 0, 0, 0, 0)

        notices: list[Notice] = []
        has_old = False
        skipped = no_title = no_date = 0

        for row in rows:
            try:
                # 제목 추출 — 제목 없는 행(헤더 등)은 정상적으로 있다. 건너뛰되 세어 둔다
                title_el = row.select_one(self.config.title_selector)
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    no_title += 1
                    continue

                # 날짜 추출
                date_el = row.select_one(self.config.date_selector)
                date_text = date_el.get_text(strip=True) if date_el else ""
                parsed_date = parse_date(date_text)

                if parsed_date is None and self.config.skip_no_date:
                    no_date += 1
                    continue

                # cutoff 비교
                if parsed_date:
                    try:
                        dt = datetime.strptime(parsed_date, "%Y-%m-%d")
                        if dt < cutoff:
                            has_old = True
                            continue
                    except ValueError:
                        pass

                # 링크 추출
                detail_url = self._extract_link(title_el)

                # start_date 변환
                start_date = None
                if parsed_date:
                    try:
                        start_date = date.fromisoformat(parsed_date)
                    except ValueError:
                        pass

                # Notice 생성
                notice = Notice(
                    source=self.config.name,
                    bid_no=self._make_bid_no(title, detail_url),
                    title=title,
                    # 기관 셀렉터가 없다 — 빈 값(v1.6.0 원칙 ②, 종전 config.name 상수. 사이트 이름은 source에 있다)
                    organization="",
                    start_date=start_date,
                    end_date=None,
                    # 게시일 판정(마감일 아님) — 원칙 ② 위반이지만 고치지 않는다(2026-09-25 사용자 "안 함": BidWatch가 배지를 숨긴다)
                    status=determine_status(parsed_date),
                    url=detail_url,
                    detail_url=detail_url,
                )
                notices.append(notice)

            except Exception:
                # 한 행의 이상 때문에 페이지 전체를 버리지 않는다 — 건너뛴 수는 _fetch가 errors로 보고한다
                logger.debug(
                    f"[{self.source_name}] 행 파싱 스킵",
                    exc_info=True,
                )
                skipped += 1
                continue

        return _RowScan(notices, has_old, skipped, len(rows), no_title, no_date)

    def _extract_link(self, title_el: Tag) -> str:
        """제목 요소에서 링크 추출."""
        # 1. <a> 요소 탐색
        link_el = title_el if title_el.name == "a" else title_el.find("a")
        if not link_el:
            return ""

        # 2. 원본 링크 추출
        raw_link = link_el.get(self.config.link_attr, "")
        if not raw_link:
            return ""

        # 3. JS regex 추출 (설정된 경우)
        if self.config.link_js_regex and self.config.link_template:
            match = re.search(self.config.link_js_regex, raw_link)
            if match:
                result = self.config.link_template
                # {id} = 첫 번째 그룹 (호환성)
                result = result.replace("{id}", match.group(1))
                # {1}, {2}, {3} = 번호별 그룹 (다중 그룹 지원)
                for i in range(1, len(match.groups()) + 1):
                    result = result.replace(f"{{{i}}}", match.group(i))
                raw_link = result

        # 4. 절대 URL 변환
        if raw_link and not raw_link.startswith(("http://", "https://")):
            base = self.config.link_base or self.config.list_url
            raw_link = urljoin(base, raw_link)

        return raw_link

    def _make_bid_no(self, title: str, link: str) -> str:
        """결정적 bid_no 생성. 포맷: SCR-{source_key}-{md5 10자리}"""
        hash_input = f"{title.strip()}{link.strip()}"
        hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:10]
        return f"SCR-{self.config.source_key}-{hash_val}"
