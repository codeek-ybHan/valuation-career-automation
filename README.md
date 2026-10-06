# Valuation Career Automation

기업가치평가(회계법인 Valuation) 취업 준비를 위한 **개인 리서치·학습 자동화 파이프라인**.

```
뉴스 검색 → 기업공시 확인 → 가치평가 관점 분석 → 학습 포인트 → 퀴즈 → 면접 소재 → Gmail 발송 → Notion 저장
```

단순 뉴스 요약기가 아니라, 매일 쌓이는 Deal·공시를 **Valuation 변수(DCF, WACC, FCF, PPA …)로 연결**해
공부 → 퀴즈 → 면접 답변 소재까지 이어 주는 시스템입니다.

## 왜 만들었나

가치평가 업무혁신 직무를 준비하며 뉴스 검색, 공시 확인, 가치평가 관점 분석, 학습·면접 소재 정리를 매일 반복했습니다.
이 반복 업무를 직접 분석해 프로세스로 정의하고 자동화했습니다.
**반복 업무 발견 → 프로세스 정의 → 자동화 → 중요한 판단에 집중**.
(실제 가치평가 프로세스 자동화를 다루는 `ValuFlow`의 전 단계에 해당하는 프로젝트입니다.)

**Human-in-the-loop**: AI는 수집·요약·연결·질문 생성·학습 지원만 합니다. 최종 가치평가 판단은 사용자가 합니다.
모든 결과에 원문 URL을 저장하고, LLM에게는 자료에 없는 사실을 지어내지 않도록 지시합니다.

## Architecture

```
[GitHub Actions cron]
        ↓
 collectors/news.py   collectors/dart.py   collectors/big4.py      ← 수집만 담당
        ↓
 repositories/processed_repository.py  (SQLite: URL + 제목 해시 중복 제거)
        ↓
 services/llm.py  (OpenAI Structured Output → Pydantic)           ← 의미 분석만 담당
        ↓
 ┌───────────────┴────────────────┐
 services/notion.py          services/gmail.py
 Intelligence DB             Daily Brief (HTML)
  └ Interview Bank
```

| 디렉토리 | 책임 | 왜 분리했나 |
|---|---|---|
| `collectors/` | 외부 데이터 수집, 키워드 점수화 | 뉴스 provider를 바꿔도 나머지는 그대로 (`NewsCollector` 인터페이스) |
| `services/llm.py` | 프롬프트 + Structured Output + retry | 분석 로직을 수집/저장과 독립적으로 개선 |
| `services/notion.py`, `gmail.py` | 저장 / 발송 | 한쪽이 실패해도 다른 쪽은 진행 (결합도 최소화) |
| `models/valuation.py` | Pydantic 스키마 | LLM 출력·저장·메일이 같은 타입을 공유 |
| `repositories/` | 처리 이력 SQLite | 매일 같은 기사를 다시 분석하지 않기 |
| `prompts/`, `templates/` | 프롬프트, HTML 메일 | 코드 수정 없이 문구/디자인 조정 |

핵심 설계 결정
- **Structured Output**: `response_format=<Pydantic>`로 스키마를 강제 → 문자열 파싱 없음. `valuation_topics`/`importance`는 `Literal`이라 허용값 밖의 값이 나올 수 없음.
- **출처는 LLM이 아니라 코드가 붙임**: `source_url`은 `SourceItem`에서 복사 (환각 방지).
- **분석 성공 시에만 '처리됨' 기록**: LLM이 실패한 항목은 다음 실행에서 자동 재시도.
- **단계 격리**: 수집/분석/Notion/Gmail 각각 try/except. Gmail이 실패해도 Notion 저장은 유지되고, 메일 HTML은 `output/`에 항상 남음.
- **의존성 최소화**: Notion·Gmail은 SDK 대신 `requests`로 REST 직접 호출 (쓰는 엔드포인트가 2~3개뿐).

## Tech Stack

Python 3.11+ · OpenAI API (Structured Output) · OpenDART API · Notion API · Gmail API (OAuth2) ·
requests · pydantic · jinja2 · SQLite · GitHub Actions

## 실행 방법

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 값 채우기

pytest                      # 단위 테스트 (외부 API 불필요)

# 단계별로 확인 (필요한 키만 있으면 됨)
python main.py sample --dry-run   # OPENAI_API_KEY만 필요. SK하이닉스 신규시설투자 샘플 분석 → output/sample_*.html
python main.py daily  --dry-run   # + 뉴스 수집 실제 호출. Notion/Gmail/이력 기록 없음
python main.py sample             # Notion + Gmail까지 실제 연동 테스트
python main.py daily              # 운영 실행
python main.py big4               # Big4 Weekly
python main.py review             # Weekly Interview Review
```

설정이 없는 서비스(Notion/Gmail/DART)는 경고만 남기고 건너뜁니다. 그래서 키를 하나씩 추가하며 단계적으로 검증할 수 있습니다.

권장 순서: ① `sample --dry-run` → ② Notion 연결 후 `sample` → ③ Gmail 연결 후 `sample` → ④ `daily --dry-run` → ⑤ `daily` → ⑥ DART 키 추가 → ⑦ Actions 등록

## 환경변수

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_MODEL` | 분석 (기본 `gpt-4o-mini`) |
| `DART_API_KEY` | OpenDART 공시 ([발급](https://opendart.fss.or.kr)). 없으면 공시 섹션 생략 |
| `NOTION_TOKEN`, `NOTION_INTELLIGENCE_DB_ID`, `NOTION_INTERVIEW_DB_ID` | Notion 저장 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `EMAIL_TO` | Gmail 발송 |
| `NEWS_COUNT`(3), `DART_COUNT`(3), `BIG4_COUNT`(5), `NEWS_LOOKBACK_HOURS`(24), `DART_LOOKBACK_DAYS`(1) | 선택 튜닝 |

`.env`는 `.gitignore`에 포함되어 있습니다. 운영에서는 GitHub Secrets만 사용합니다.

### Gmail refresh token 발급 (1회)

1. Google Cloud Console에서 Gmail API 활성화 → OAuth 동의 화면(테스트 사용자에 본인 추가) → OAuth 클라이언트 ID(**데스크톱 앱**) 생성 후 JSON 다운로드
2. `pip install google-auth-oauthlib && python scripts/get_gmail_refresh_token.py client_secret.json`
3. 출력된 3개 값을 `.env`와 GitHub Secrets에 저장 (요청 권한은 `gmail.send` 하나뿐)

> OAuth 동의 화면이 "테스트" 상태이면 refresh token이 7일 후 만료됩니다. 장기 운영하려면 앱을 "프로덕션"으로 게시하세요(개인용은 검증 심사 없이 가능, "확인되지 않은 앱" 경고만 표시).

## Notion DB 구성

Notion에서 DB 2개를 만들고, 해당 Integration을 각 DB에 **Connections로 초대**하세요. 속성 이름과 **타입**이 아래와 정확히 같아야 합니다.

**Valuation Intelligence**

| 속성 | 타입 | 값 |
|---|---|---|
| Name | Title | |
| Date | Date | |
| Type | Select | `M&A` `공시` `Big4` `공부` |
| Company | Text | |
| Topic | Multi-select | `DCF` `WACC` `FCF` `CAPEX` `PPA` `Impairment` `Fair Value` `Comparable` `M&A` `AI/업무혁신` |
| Source | URL | |
| Summary, Valuation Impact, Study Point | Text | |
| Interview Worthy | Checkbox | |
| Importance | Select | `★★★` `★★` `★` |
| Status | **Select** | `안 봄` `공부함` `면접정리` (신규는 `안 봄`) |

**Valuation Interview Bank**

| 속성 | 타입 | 값 |
|---|---|---|
| Name | Title | |
| Date | Date | |
| Category | Select | `최근 M&A` `기업가치 영향요인` `Valuation 이슈` `AI/업무혁신` `지원동기` `기술질문` |
| Question, My Answer Material | Text | |
| Related Company, Related Concept | Text | |
| Source | URL | |
| Ready | Checkbox | 초기 false. 직접 정리했을 때만 체크 |

> `Status`를 Notion의 "Status" 타입으로 만들었다면 `services/notion.py`의 `"Status": {"select": ...}`를 `{"status": ...}`로 바꾸세요.

## GitHub Actions 설정

1. 이 폴더를 GitHub 저장소 루트로 push
2. Settings → Secrets and variables → Actions에 아래 Secrets 등록
   `OPENAI_API_KEY` `DART_API_KEY` `NOTION_TOKEN` `NOTION_INTELLIGENCE_DB_ID` `NOTION_INTERVIEW_DB_ID` `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `GOOGLE_REFRESH_TOKEN` `EMAIL_TO`
3. Actions 탭에서 각 workflow를 `Run workflow`로 수동 실행해 확인

| Workflow | KST | cron (UTC) |
|---|---|---|
| `daily.yml` | 월~금 08:00 | `0 23 * * 0-4` (UTC 일~목 23시 = KST 월~금 08시) |
| `big4-weekly.yml` | 금 08:00 | `0 23 * * 4` |
| `interview-review.yml` | 일 09:00 | `0 0 * * 0` |

중복 제거용 `data/processed.db`는 `actions/cache`로 실행 간에 유지합니다.
(캐시는 7일간 미사용 시 삭제될 수 있으나, 매일 실행되므로 유지됩니다. 영구 보관이 필요하면 Notion을 이력 저장소로 쓰거나 DB를 외부로 옮기세요.)

## Daily Brief 구성

제목: `[Valuation Brief] YYYY.MM.DD | 오늘의 Deal · 공시 · Quiz`

1. 오늘의 M&A / Valuation 기사 3개 → 2. 오늘의 공시 → Valuation Point (없으면 생략) → 3. 오늘 공부할 개념 →
4. Valuation Quiz 3문제 → 5. 정답/해설 → 6. 오늘의 면접 소재 → 7. 원문 Source

`python main.py sample --dry-run` 후 `output/sample_*.html`을 브라우저로 열어 실제 모양을 볼 수 있습니다.

## 알려진 한계 / 향후 개선

- 뉴스는 Google News RSS의 **제목 + 요약 스니펫**만 LLM에 전달합니다(본문 크롤링 없음). 그래서 프롬프트가 "자료에 없는 사실은 확인 필요로 표기"하도록 강제합니다. → 본문 추출(trafilatura 등) 또는 네이버/NewsAPI provider 추가 (`NewsCollector` 상속)
- 공시는 목록 메타데이터만 분석합니다. → OpenDART 본문(`document.xml`) 파싱으로 금액·비율 반영
- Big4는 `site:` 뉴스 검색 기반이라 누락이 있을 수 있습니다. → 법인별 Insights 페이지 스크래핑/RSS
- 퀴즈 풀이 결과(정답률)를 Notion에 기록해 약한 개념 자동 추적
- 처리 이력 DB를 외부 저장소(Notion/Supabase 등)로 이전
- 다른 스케줄러로 확장: `python main.py <command>` 한 줄이라 cron / Render Cron / Cloud Run Job에 그대로 사용 가능
