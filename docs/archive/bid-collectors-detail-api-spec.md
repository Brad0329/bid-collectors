# bid-collectors: 상세 조회 API 개발 요청

## 목적

BidWatch에서 공고 목록 클릭 시 모달 팝업으로 상세 정보를 보여줄 때, **수집 시점에 가져오지 못한 추가 정보를 실시간으로 가져오는** `fetch_detail()` 메서드가 필요합니다.

현재 수집(collect)은 **목록 API**만 호출하여 기본 정보만 저장합니다. 사용자가 특정 공고를 클릭할 때만 **상세 API**를 호출하여 사업개요, 추가 첨부파일 등을 가져오고, 결과를 DB에 캐시합니다.

---

## 1. NaraCollector.fetch_detail(bid_no) — 나라장터 상세 조회

### 현재 상황
- 목록 API(`getBidPblancListInfo*`)로 수집 중
- **사업개요(content)가 목록 API에 없음** — 가장 중요한 누락 정보
- 첨부파일은 목록 API에서 `bidNtceFlUrl1~10`으로 이미 수집 중
- 추가로 가져올 수 있는 필드: 입찰참가자격, 배정예산 상세, 계약조건 등

### 상세 API 정보

나라장터 공공데이터포털에 **상세 조회 API**가 있을 것으로 추정됩니다:

```
서비스: BidPublicInfoService (기존과 동일 서비스)
Operation 후보:
  - getBidPblancDtlInfo[Servc|Thng|Cnstwk] (상세)
  - 또는 다른 operation명

확인 필요: data.go.kr에서 "입찰공고정보서비스" 활용가이드 문서에서 
상세 조회 operation 확인
```

**확인해야 할 사항:**
1. data.go.kr > 나라장터 입찰공고정보서비스 > 활용가이드(문서)에서 상세 조회 operation 이름 확인
2. 상세 API가 없으면, 웹 페이지 스크래핑 검토 (g2b.go.kr 상세 페이지)

### 메서드 시그니처

```python
class NaraCollector(BaseCollector):
    
    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회.
        
        Args:
            bid_no: 공고번호. 
                    수집 시 저장된 형식: "용역-R26BK01457928-000"
                    API에 전달할 형식: bidNtceNo="R26BK01457928", bidNtceOrd="000"
                    → bid_no에서 bid_type 접두사와 "-" 구분자로 파싱 필요
        
        Returns:
            dict | None: 성공 시 아래 키를 포함하는 dict, 실패 시 None
            {
                "content": str,        # 사업개요/공고내용 (핵심!)
                "attachments": list,   # [{"name": "...", "url": "..."}, ...] 추가 첨부파일
                # 아래는 상세 API에서 제공하는 경우만:
                "bid_qual_content": str,  # 입찰참가자격
                "assign_budget": int,     # 배정예산 상세
                "contract_condition": str, # 계약조건
                ... (API에서 제공하는 추가 필드)
            }
        """
```

### bid_no 파싱 로직

현재 nara.py에서 bid_no를 이렇게 생성합니다 (line 134):
```python
bid_no=f"{bid_type}-{full_bid_no}"  
# 예: "용역-R26BK01457928-000"
```

fetch_detail에서 파싱:
```python
# "용역-R26BK01457928-000" → bid_type="용역", bidNtceNo="R26BK01457928", bidNtceOrd="000"
parts = bid_no.split("-", 1)  # ["용역", "R26BK01457928-000"]
bid_type = parts[0]
bid_no_rest = parts[1]  # "R26BK01457928-000"
# bidNtceNo와 bidNtceOrd 분리
last_dash = bid_no_rest.rfind("-")
ntce_no = bid_no_rest[:last_dash]   # "R26BK01457928"
ntce_ord = bid_no_rest[last_dash+1:] # "000"
```

### 구현 패턴

기존 `_request_with_retry()` 메서드를 재활용할 수 있습니다:

```python
async def fetch_detail(self, bid_no: str) -> dict | None:
    # 1. bid_no 파싱 → bid_type, ntce_no, ntce_ord
    # 2. bid_type에 따라 operation 결정 (용역→Servc, 물품→Thng, 공사→Cnstwk)
    # 3. 상세 API 호출 (params: bidNtceNo, bidNtceOrd)
    # 4. XML 파싱 → dict 반환
    # 5. content(사업개요), 추가 attachments, 기타 상세 필드 추출
```

### 기존 코드 재활용

| 기존 코드 | 위치 | 용도 |
|-----------|------|------|
| `_request_with_retry()` | nara.py line 350 | API 호출 + 429 재시도 |
| `_parse_xml_items()` | nara.py line 80 | XML 파싱 |
| `create_client()` | utils/http.py | httpx 클라이언트 |

---

## 2. KstartupCollector.fetch_detail(bid_no) — K-Startup 상세 조회

### 현재 상황
- 목록 API에서 content를 이미 가져오지만, **500자로 잘려있음** (kstartup.py line 136)
- 단건 필터로 전체 content를 가져올 수 있음
- lets_portal에서 이미 구현됨 (참고용)

### API 호출 방법

```python
# 기존 목록 API와 동일 엔드포인트, 단건 필터 추가
API_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"

params = {
    "serviceKey": self.api_key,
    "page": "1",
    "perPage": "1",
    "returnType": "json",
    "cond[pbanc_sn::EQ]": pbanc_id,  # bid_no에서 추출한 ID
}
```

### bid_no 파싱

```python
# "KSTARTUP-12345" → pbanc_id = "12345"
pbanc_id = bid_no.replace("KSTARTUP-", "")
```

### 메서드 시그니처

```python
class KstartupCollector(BaseCollector):
    
    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회.
        
        Args:
            bid_no: "KSTARTUP-12345" 형식
        
        Returns:
            dict | None: 성공 시 아래 키를 포함하는 dict
            {
                "content": str,         # 사업개요 전문 (500자 제한 없이)
                "target": str,          # 지원대상
                "target_age": str,      # 대상연령
                "biz_year": str,        # 창업연차
                "excl_target": str,     # 제외대상
                "apply_method": str,    # 접수방법
                "department": str,      # 담당부서
                "contact": str,         # 문의처
                "biz_name": str,        # 통합공고 사업명
                "apply_url": str,       # 신청 URL
            }
        """
```

### 구현 시 주의사항

- HTML 엔티티 디코딩 필요: `html.unescape()`
- `<br>` → 줄바꿈 변환: `re.sub(r"<br\s*/?>", "\n", text)`
- HTML 태그 제거: `re.sub(r"<[^>]+>", "", text)`
- 기존 `clean_html()`, `clean_html_to_text()` 유틸 재활용 가능 (utils/text.py)

---

## 3. BaseCollector에 fetch_detail 인터페이스 추가

```python
# base.py에 추가
class BaseCollector(ABC):
    
    async def fetch_detail(self, bid_no: str) -> dict | None:
        """단일 공고 상세 조회. 지원하지 않는 수집기는 None 반환."""
        return None
```

기본 구현은 `None` 반환 — 상세 조회를 지원하지 않는 수집기(기업마당, 보조금24 등)는 오버라이드 불필요.

---

## 4. BidWatch에서 호출하는 방식

```python
# BidWatch 백엔드에서 모달 열 때:
from bid_collectors import NaraCollector, KstartupCollector

# collector_type에 따라 수집기 인스턴스 생성
collector = NaraCollector(api_key=settings.DATA_GO_KR_KEY)
detail = await collector.fetch_detail(notice.bid_no)

if detail:
    # content 업데이트
    if detail.get("content"):
        notice.content = detail["content"]
    # extra 병합
    extra = dict(notice.extra or {})
    extra.update({k: v for k, v in detail.items() if k != "content" and v})
    notice.extra = extra
    # DB 저장 (캐시)
    await db.commit()
```

---

## 5. 테스트

```python
# tests/test_nara_detail.py
import pytest
from bid_collectors import NaraCollector

@pytest.mark.integration
async def test_nara_fetch_detail():
    collector = NaraCollector()  # 환경변수에서 키 로드
    # 실제 존재하는 공고번호로 테스트
    detail = await collector.fetch_detail("용역-R26BK01457928-000")
    assert detail is not None
    assert "content" in detail
    print(f"content: {detail['content'][:100]}")

@pytest.mark.integration
async def test_kstartup_fetch_detail():
    collector = KstartupCollector()
    detail = await collector.fetch_detail("KSTARTUP-12345")
    # 존재하지 않는 ID면 None
    # 존재하면 content 포함
```

---

## 우선순위

1. **KstartupCollector.fetch_detail** — API 확실히 동작, 구현 간단
2. **NaraCollector.fetch_detail** — 상세 API operation 확인 필요, 없으면 대안 검토

## 참고 파일

- `bid_collectors/nara.py` — 현재 NaraCollector 전체 코드
- `bid_collectors/kstartup.py` — 현재 KstartupCollector 전체 코드
- `bid_collectors/base.py` — BaseCollector 인터페이스
- `bid_collectors/utils/text.py` — clean_html, clean_html_to_text
- `bid_collectors/utils/http.py` — create_client (httpx)
