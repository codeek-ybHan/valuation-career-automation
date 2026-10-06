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

# 기획서 7장의 우선 키워드. 제목에 많이 걸릴수록 점수가 높다.
KEYWORDS = [
    "M&A", "인수", "합병", "매각", "IPO", "기업가치", "valuation", "acquisition",
    "merger", "deal", "private equity", "PEF", "restructuring", "spin-off",
    "PPA", "impairment", "fair value", "enterprise value",
]

KO_QUERY = "(M&A OR 인수 OR 합병 OR 매각 OR 기업가치 OR IPO OR PEF)"
EN_QUERY = '(acquisition OR merger OR "private equity" OR valuation OR "enterprise value")'

_HANGUL = re.compile(r"[가-힣]")
_TAG = re.compile(r"<[^>]+>")


class NewsCollector(ABC):
    """provider 교체를 위한 인터페이스."""

    @abstractmethod
    def collect(self) -> list[SourceItem]:
        ...


def score_title(text: str) -> float:
    """키워드 매칭 수 + 한국어(국내 거래 우선) 가산점."""
    lower = text.lower()
    score = sum(1.0 for kw in KEYWORDS if kw.lower() in lower)
    if _HANGUL.search(text):
        score += 1.5
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
            score=score_title(title),
        ))
    return items


class GoogleNewsCollector(NewsCollector):
    def __init__(self, lookback_hours: int = 24, limit: int = 3):
        self.lookback = timedelta(hours=lookback_hours)
        self.limit = limit

    def collect(self) -> list[SourceItem]:
        """후보 전체를 점수순으로 반환한다. 최종 N건 선정은 중복 제거 후 main에서 한다."""
        candidates: list[SourceItem] = []
        for query, lang in ((KO_QUERY, "ko"), (EN_QUERY, "en")):
            try:
                found = fetch_google_news(
                    f"{query} when:{max(1, self.lookback.days or 1)}d",
                    kind="news", lookback=self.lookback, lang=lang,
                )
                candidates.extend(found)
            except Exception as e:  # 한 쿼리 실패가 전체를 막지 않게
                log.error("News fetch failed (%s): %s", lang, e)

        # 같은 URL 제거 후 점수 내림차순, 최신순 보조 정렬
        unique = {i.url: i for i in candidates}
        ranked = sorted(
            unique.values(),
            key=lambda i: (i.score, i.published_at or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        return [i for i in ranked if i.score > 0]


def collect_news(lookback_hours: int = 24) -> list[SourceItem]:
    return GoogleNewsCollector(lookback_hours=lookback_hours).collect()
