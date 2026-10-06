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
KO_TOPICS = '(가치평가 OR M&A OR 딜 OR 공정가치 OR PPA OR 손상 OR 기업금융 OR AI OR 업무혁신)'

# 채용 공고는 리서치 자료가 아니므로 제외 (예: "Deals Manager in CA-Silicon Valley")
JOB_POSTING = re.compile(
    r"채용|인턴|\bExperienced\b|\bIntern(ship)?\b|\bin [A-Z]{2}-\S|\b(Associate|Manager|Director|Analyst)\b",
    re.I,
)

# 국내 법인 자료(한글) 우선. 글로벌(영문) 자료는 국내 자료가 부족할 때만 보충한다.
FIRMS: dict[str, list[tuple[str, str]]] = {
    "삼일PwC": [
        (f"(site:samil.com OR site:pwc.com/kr) {KO_TOPICS}", "ko"),
        (f"삼일PwC {KO_TOPICS}", "ko"),
    ],
    "삼정KPMG": [
        (f"(site:kpmg.com/kr OR site:home.kpmg/kr) {KO_TOPICS}", "ko"),
        (f"삼정KPMG {KO_TOPICS}", "ko"),
    ],
    "딜로이트안진": [
        (f"site:deloitte.com/kr {KO_TOPICS}", "ko"),
        (f"딜로이트 안진 {KO_TOPICS}", "ko"),
    ],
    "EY한영": [
        (f"site:ey.com/ko_kr {KO_TOPICS}", "ko"),
        (f"EY한영 {KO_TOPICS}", "ko"),
    ],
}

# 글로벌 보충용 (국내 자료가 부족할 때만 사용, 기획서 19장: 글로벌 10% 이하)
GLOBAL_FIRMS: dict[str, tuple[str, str]] = {
    "PwC Global": (f"site:pwc.com {TOPICS}", "en"),
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

    # 국내 자료가 부족할 때만 글로벌 자료로 보충 (최대 limit의 10%, 최소 1건)
    if len(picked) < limit:
        quota = min(limit - len(picked), max(1, limit // 10))
        for firm, (query, lang) in GLOBAL_FIRMS.items():
            try:
                extra = [i for i in fetch_google_news(f"{query} when:{lookback_days}d",
                                                       kind="big4", lookback=lookback, lang=lang)
                         if not JOB_POSTING.search(i.title)]
            except Exception as e:
                log.error("Global Big4 fetch failed (%s): %s", firm, e)
                continue
            for item in sorted(extra, key=lambda i: i.score, reverse=True)[:quota]:
                item.content = f"법인: {firm} (글로벌 참고자료)\n" + item.content
                picked.append(item)
            break
    return picked
