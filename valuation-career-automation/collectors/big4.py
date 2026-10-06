"""Big4(삼일PwC / KPMG / Deloitte / EY) 최신 자료 수집.

Big4 사이트는 통일된 RSS가 없어서 Google News RSS의 site: 검색을 재사용한다.
각 법인에서 골고루 뽑기 위해 법인별로 후보를 모은 뒤 round-robin으로 선정한다.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from collectors.news import fetch_google_news
from models.valuation import SourceItem

log = logging.getLogger(__name__)

TOPICS = ('(valuation OR deals OR "M&A" OR PPA OR impairment OR "fair value" '
          'OR transaction OR "corporate finance" OR AI OR analytics OR automation)')

# 채용 공고는 리서치 자료가 아니므로 제외 (예: "Deals Manager in CA-Silicon Valley")
JOB_POSTING = re.compile(
    r"채용|인턴|\bExperienced\b|\bIntern(ship)?\b|\bin [A-Z]{2}-\S|\b(Associate|Manager|Director|Analyst)\b",
    re.I,
)

FIRMS: dict[str, list[tuple[str, str]]] = {
    # 법인명: [(쿼리, 언어), ...]
    "PwC": [
        (f"(site:pwc.com OR site:samil.com) {TOPICS}", "en"),
        ('삼일PwC (가치평가 OR M&A OR 딜 OR 공정가치)', "ko"),
    ],
    "KPMG": [
        (f"site:kpmg.com {TOPICS}", "en"),
        ('삼정KPMG (가치평가 OR M&A OR 딜 OR 공정가치)', "ko"),
    ],
    "Deloitte": [
        (f"site:deloitte.com {TOPICS}", "en"),
        ('딜로이트 안진 (가치평가 OR M&A OR 딜 OR 공정가치)', "ko"),
    ],
    "EY": [
        (f"site:ey.com {TOPICS}", "en"),
        ('EY한영 (가치평가 OR M&A OR 딜 OR 공정가치)', "ko"),
    ],
}


def collect_big4(lookback_days: int = 7, limit: int = 5) -> list[SourceItem]:
    lookback = timedelta(days=lookback_days)
    per_firm: dict[str, list[SourceItem]] = {}

    for firm, queries in FIRMS.items():
        found: dict[str, SourceItem] = {}
        for query, lang in queries:
            try:
                for item in fetch_google_news(f"{query} when:{lookback_days}d",
                                              kind="big4", lookback=lookback, lang=lang):
                    if JOB_POSTING.search(item.title):
                        continue
                    item.content = f"법인: {firm}\n" + item.content
                    found[item.url] = item
            except Exception as e:
                log.error("Big4 fetch failed (%s): %s", firm, e)
        per_firm[firm] = sorted(found.values(), key=lambda i: i.score, reverse=True)

    # 법인별 1순위 → 2순위 … 순으로 번갈아 선택해 한 법인 쏠림을 막는다.
    picked: list[SourceItem] = []
    depth = 0
    while len(picked) < limit and any(len(v) > depth for v in per_firm.values()):
        for items in per_firm.values():
            if len(items) > depth and len(picked) < limit:
                picked.append(items[depth])
        depth += 1
    return picked
