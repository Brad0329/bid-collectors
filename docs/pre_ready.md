# bid-collectors 개발 전 준비사항

> 개발 시작 전에 아래 항목을 준비해야 합니다.
> 대부분 무료이며, API 키 발급에 1~3일 소요될 수 있으므로 **미리 신청**해 두세요.

---

## 1. 공공데이터포털 (data.go.kr) 계정 + API 키

### 1-1. 계정 가입

- **사이트:** https://www.data.go.kr
- 회원가입 (개인 또는 기업)
- 가입 후 로그인

### 1-2. API 활용 신청 — Phase 1 (MVP)

아래 3개 API를 **먼저 활용신청**하세요. 대부분 즉시/자동 승인입니다.

| # | API명 | data.go.kr ID | 신청 URL | 승인 |
|---|--------|:------------:|----------|:----:|
| 1 | **조달청 입찰공고정보서비스** | 15129394 | [신청](https://www.data.go.kr/data/15129394/openapi.do) | 자동 |
| 2 | **기업마당 지원사업정보** | - | [bizinfo.go.kr API](https://www.bizinfo.go.kr/web/lay1/program/S1T175C174/apiDetail.do?id=bizinfoApi) | 자동 |
| 3 | **보조금24 공공서비스 정���** | 15113968 | [신청](https://www.data.go.kr/data/15113968/openapi.do) | 자동 |

**승인 후 발급되는 것:**
- 일반 인증키 (Encoding/Decoding 2종)
- 개발계정 트래픽: 일 1,000건 (나라장터는 일부 100건)

### 1-3. API 활용 신청 — Phase 2 (확장, 여유 있을 때)

| # | API명 | data.go.kr ID | 승인 |
|---|--------|:------------:|:----:|
| 4 | LH 입찰공고정보 | 15021183 | 자동 |
| 5 | LH 계약현황정보 | 15021184 | 자동 |
| 6 | LH 발주계획정보 | 15042795 | 자동 |
| 7 | LH 사전규격공개정보 | 15042796 | 자동 |
| 8 | LH 개찰정보 | 15057180 | 자동 |
| 9 | 한국전력 전자입찰계약정보 | 15148223 | 자동 |
| 10 | 한국도로공사 계약공개현황 | 15128076 | 자동 |
| 11 | 한국수자원공사 입찰공고 | 15101635 | 자동 |
| 12 | 방위사업청 입찰공고 | 15002040 | 자동 |
| 13 | 방위사업청 입찰결과 | 15002018 | 자동 |
| 14 | 방위사업청 조달계획 | 15002017 | 자동 |
| 15 | 방위사업청 계약정보 | 15002019 | 자동 |

### 1-4. API 활용 신청 — Phase 3 (부가)

| # | API명 | data.go.kr ID | 승인 |
|---|--------|:------------:|:----:|
| 16 | K-Startup 조회서비스 | 15125364 | 자동 |
| 17 | 중소벤처24 공고정보 | 15113191 | 자동 |
| 18 | 조달청 낙찰정보서비스 | 15129397 | 자동 |
| 19 | 조달청 계약정보서비스 | 15129427 | 자동 |
| 20 | 조달청 사전규격정보서비스 | 15129437 | 자동 |
| 21 | 조달청 계약과정통합공개서비스 | 15129459 | 자동 |

> **팁:** 한 번에 여러 개 신청 가능합니다. Phase 2~3도 미리 신청해두면 나중에 바로 개발 가능.

---

## 2. 환경변수 설정

발급받은 API 키를 `.env` 파일에 설정합니다.

```bash
# .env

# 공공데이터포털 통합 인증키 (대부분의 API가 이 키 하나로 동작)
DATA_GO_KR_KEY=발급받은_일반인증키_Decoding

# 기업마당 (별도 키가 필요한 경우)
BIZINFO_API_KEY=발급받은_키

# 나라장터 (data.go.kr 키와 동일할 수 있음, API별 확인 필요)
NARA_API_KEY=발급받은_키
```

> **참고:** data.go.kr���서 발급받은 키 1개로 여러 API에 사용 가능한 경우가 많습니다.
> 단, 기업마당(bizinfo.go.kr)은 별도 사이트이므로 별도 키가 필요할 수 있습니다.

---

## 3. 개발 환경

### 3-1. Python

```
Python 3.11 이상 (3.12 권장)
```

확인:
```bash
python --version
```

### 3-2. 가상환경 + 의존성

```bash
cd bid-collectors
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3-3. 도구

- **테스트:** pytest + pytest-asyncio
- **린트:** ruff
- **HTTP 디버깅:** httpx (또는 Postman/Insomnia로 API 수동 테스트)

---

## 4. API 수동 테스트 (선택, 권장)

개발 전에 각 API를 수동으로 호출해보면 응답 구조를 빠르게 파악할 수 있습니다.

### 4-1. 나라장터 테스트

```bash
# 용역 입찰공고 조회 (최근 1일)
curl "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch?ServiceKey=YOUR_KEY&inqryBgnDt=20260404&inqryEndDt=20260405&numOfRows=5&pageNo=1"
```

- 응답: **XML**
- 주요 필드: bidNtceNm(공고명), ntceInsttNm(기관), bidClseDt(마감일), presmptPrce(추정가격)

### 4-2. 기업마당 테스트

```bash
curl "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do?crtfcKey=YOUR_KEY&dataType=json&pageUnit=5&pageIndex=1"
```

- 응답: **JSON**
- 주요 필드 확인

### 4-3. 보조금24 테스트

```bash
curl "https://apis.data.go.kr/B554287/LocalGovernmentService/getLocalGovernmentList?serviceKey=YOUR_KEY&numOfRows=5&pageNo=1&type=json"
```

- 응답: **JSON**

> **팁:** Postman이나 브라우저에서 URL을 직접 열어보면 응답 구조를 빠르게 확인 가능

---

## 5. lets_portal 원본 코드 접근 (선택)

dev_reference.md에 핵심 코드 구조가 정리되어 있으므로 원본을 열 필요는 없지만, 필요 시:

```
원본 위치: C:\Users\user\Documents\lets_portal\backend\
주요 파일:
  - collectors/nara.py           나라장터 수집기
  - collectors/kstartup.py       K-Startup 수집기
  - collectors/generic_scraper.py 범용 스크래퍼
  - collectors/base.py           BaseCollector
  - utils/                       유틸리티
```

---

## 6. 운영 전환 시 추가 준비 (나중)

개발 완료 후 실제 서비스에 투입할 때:

- **트래픽 증량 신청:** data.go.kr에서 개발계정 → 운영계정 전환 (일 호출량 증가)
- **IP 화이트리스트:** 일부 API는 서버 IP 등록 필요
- **모니터링:** API 키 만료/갱신 주기 확인 (보통 1~2년)

---

## 체크리스트 요약

### 즉시 해야 할 것 (Phase 1 시작 전)

- [ ] data.go.kr 회원가입
- [ ] 조달청 입찰공고정보서비스 활용신청 (15129394)
- [ ] 기업마당 API 키 발급 (bizinfo.go.kr)
- [ ] 보조금24 API 활용신청 (15113968)
- [ ] Python 3.11+ 설치 확인
- [ ] `.env` 파일에 API 키 설정
- [ ] 각 API 수동 호출 테스트 (curl/Postman)

### 미리 해두면 좋은 것 (Phase 2 시작 전까지)

- [ ] LH API 5개 활용신청
- [ ] 한국전력/도로공사/수자원공사 API 활용신청
- [ ] 방위사업청 API 4개 활용신청
- [ ] K-Startup API 활용신청 (15125364)
- [ ] 중소벤처24 API 활용신청 (15113191)
- [ ] 조달청 낙찰/계약/사전규격 API 활용신청
