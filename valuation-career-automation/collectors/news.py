"""뉴스 수집.

MVP provider는 Google News RSS다. API 키가 필요 없고 구조가 안정적이라 선택했다.
provider를 바꾸고 싶으면 `NewsCollector`를 상속해 collect()만 구현하면 된다
(예: 네이버 검색 API, NewsAPI).

Collector는 '수집 + 키워드 점수화'까지만 한다. 의미 분석은 LLM의 몫이다.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote

import requests

from models.valuation import SourceItem, SourceKind

log = logging.getLogger(__name__)

# 기획서 7장의 한국어 우선 키워드. 제목에 많이 걸릴수록 점수가 높다.
KEYWORDS = [
    "기업가치", "가치평가", "M&A", "인수합병", "인수", "매각", "지분 인수", "지분 매각",
    "PEF", "사모펀드", "IPO", "기업공개", "합병", "분할", "주식교환", "영업양수도",
    "구조조정", "자회사 매각", "사업부 매각", "투자유치", "유상증자", "전환사채",
    "신규시설투자", "CAPEX", "공정가치", "손상평가", "PPA", "영업권",
]

# Google News RSS 쿼리 길이를 고려해 키워드를 묶어서 검색한다. (국내 뉴스만)
KO_QUERIES = [
    "(M&A OR 인수합병 OR 인수 OR 매각 OR 지분인수 OR 지분매각 OR 기업가치)",
    "(PEF OR 사모펀드 OR IPO OR 기업공개 OR 합병 OR 분할 OR 주식교환 OR 영업양수도)",
    "(구조조정 OR 사업부매각 OR 자회사매각 OR 투자유치 OR 유상증자 OR 전환사채)",
    "(신규시설투자 OR CAPEX OR 공정가치 OR 손상평가 OR PPA OR 영업권)",
]

# 기획서 26장 Source Score: 국내 주요 경제언론 3점, 그 외 일반 언론은 감점
DOMESTIC_ECON_MEDIA = (
    "한국경제", "매일경제", "서울경제", "연합인포맥스", "머니투데이", "이데일리",
    "파이낸셜뉴스", "아시아경제", "조선비즈", "헤럴드경제", "연합뉴스", "더벨", "thebell",
)

_HANGUL = re.compile(r"[가-힣]")
_TAG = re.compile(r"<[^>]+>")


class NewsCollector(ABC):
    """provider 교체를 위한 인터페이스."""

    @abstractmethod
    def collect(self) -> list[SourceItem]:
        ...


def score_title(text: str, publisher: str | None = None) -> float:
    """키워드 매칭 수 + 한국어(국내 기업 우선) 가산점 + 국내 경제언론 가산점."""
    lower = text.lower()
    score = sum(1.0 for kw in KEYWORDS if kw.lower() in lower)
    if _HANGUL.search(text):
        score += 1.5
    if publisher and any(m.lower() in publisher.lower() for m in DOMESTIC_ECON_MEDIA):
        score += 1.0
    return score


def fetch_google_news(
    query: str,
    *,
    kind: SourceKind,
    lookback: timedelta,
    lang: str = "ko",
    timeout: int = 15,
) -> list[SourceItem]:
    """Google News RSS 한 쿼리를 가져와 lookback 기간 내 기사만 SourceItem으로 반환."""
    if lang == "ko":
        params = "hl=ko&gl=KR&ceid=KR:ko"
    else:
        params = "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={quote(query)}&{params}"

    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return parse_rss(resp.content, kind=kind, lookback=lookback)


def parse_rss(xml_bytes: bytes, *, kind: SourceKind, lookback: timedelta,
              now: datetime | None = None) -> list[SourceItem]:
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml_bytes)
    items: list[SourceItem] = []

    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue

        published = None
        pub_text = node.findtext("pubDate")
        if pub_text:
            try:
                published = parsedate_to_datetime(pub_text)
            except (TypeError, ValueError):
                published = None
        if published and now - published > lookback:
            continue

        publisher = (node.findtext("source") or "").strip() or None
        # Google News 제목은 "기사 제목 - 언론사" 형태 → 언론사 부분 분리
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")]

        snippet = unescape(_TAG.sub(" ", node.findtext("description") or ""))
        snippet = re.sub(r"\s+", " ", snippet).strip()

        items.append(SourceItem(
            title=title,
            url=link,
            kind=kind,
            publisher=publisher,
            published_at=published,
            content=f"제목: {title}\n언론사: {publisher or '알 수 없음'}\n관련 내용: {snippet}",
            score=score_title(title, publisher),
        ))
    return items


class GoogleNewsCollector(NewsCollector):
    def __init__(self, lookback_hours: int = 24, limit: int = 3):
        self.lookback = timedelta(hours=lookback_hours)
        self.limit = limit

    def collect(self) -> list[SourceItem]:
        """후보 전체를 점수순으로 반환한다. 최종 N건 선정은 중복 제거 후 main에서 한다."""
        candidates: list[SourceItem] = []
        for query in KO_QUERIES:
            try:
                found = fetch_google_news(
                    f"{query} when:{max(1, self.lookback.days or 1)}d",
                    kind="news", lookback=self.lookback, lang="ko",
                )
                candidates.extend(found)
            except Exception as e:  # 한 쿼리 실패가 전체를 막지 않게
                log.error("News fetch failed (%s): %s", query, e)

        # 같은 URL 제거 후 점수 내림차순, 최신순 보조 정렬
        unique = {i.url: i for i in candidates}
        ranked = sorted(
            unique.values(),
            key=lambda i: (i.score, i.published_at or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        # 한글 제목(국내 기사)만 후보로 사용: 글로벌 기사는 기본값에서 제외 (기획서 2장)
        return [i for i in ranked if i.score > 0 and _HANGUL.search(i.title)]


def collect_news(lookback_hours: int = 24) -> list[SourceItem]:
    return GoogleNewsCollector(lookback_hours=lookback_hours).collect()
