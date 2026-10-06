"""OpenDART 공시 수집.

list.json으로 최근 공시 목록을 받아 '기업가치에 직접 영향이 큰' 공시명만 남긴다.
공시 본문 파싱은 MVP 범위 밖이므로, 목록 메타데이터(회사/공시명/시장)를 LLM에 넘기고
LLM은 '재무영향 → Valuation 변수 → 기업가치 영향 → 추가 확인사항' 틀로 분석한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from config import KST
from models.valuation import SourceItem

log = logging.getLogger(__name__)

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 기획서 8장 키워드 → 가중치 (구조 변화가 큰 공시일수록 높게)
KEYWORD_WEIGHTS = {
    "합병": 5, "영업양수": 5, "영업양도": 5, "주식교환": 5, "분할": 4,
    "타법인주식및출자증권취득결정": 4, "타법인주식및출자증권처분결정": 4,
    "유상증자": 3, "전환사채": 3, "신주인수권부사채": 3,
    "신규시설투자": 3, "자기주식": 2, "대규모기업집단": 1,
}
# 시장구분 코드: Y 유가증권, K 코스닥, N 코넥스, E 기타
CORP_CLS_LABEL = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "기타"}
CORP_CLS_BONUS = {"Y": 2, "K": 1}


def match_keyword(report_nm: str) -> tuple[str, int] | None:
    """공시명(공백 제거)에 키워드가 있으면 (키워드, 가중치) 중 가장 높은 것을 반환."""
    name = report_nm.replace(" ", "")
    hits = [(kw, w) for kw, w in KEYWORD_WEIGHTS.items() if kw in name]
    return max(hits, key=lambda h: h[1]) if hits else None


def to_item(row: dict) -> SourceItem | None:
    hit = match_keyword(row.get("report_nm", ""))
    if not hit:
        return None
    keyword, weight = hit
    corp = row.get("corp_name", "")
    report = row.get("report_nm", "").strip()
    rcept_no = row.get("rcept_no", "")
    market = CORP_CLS_LABEL.get(row.get("corp_cls", ""), "기타")
    rcept_dt = row.get("rcept_dt", "")

    published = None
    if rcept_dt:
        try:
            published = datetime.strptime(rcept_dt, "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            pass

    return SourceItem(
        title=f"[공시] {corp} - {report}",
        url=VIEW_URL.format(rcept_no=rcept_no),
        kind="dart",
        publisher="DART",
        published_at=published,
        company=corp,
        content=(
            f"회사: {corp} ({market})\n공시명: {report}\n접수일자: {rcept_dt}\n"
            f"해당 키워드: {keyword}\n"
            "※ 공시 본문은 제공되지 않았다. 공시명과 회사 정보로 알 수 있는 범위만 분석하고, "
            "금액·비율 등 본문에만 있는 정보는 추측하지 말고 '추가 확인사항'에 적어라."
        ),
        score=weight + CORP_CLS_BONUS.get(row.get("corp_cls", ""), 0),
    )


def collect_dart_disclosures(api_key: str, lookback_days: int = 1,
                             max_pages: int = 5) -> list[SourceItem]:
    """최근 lookback_days일 공시 중 관심 공시만 점수순으로 반환."""
    today = datetime.now(KST).date()
    params = {
        "crtfc_key": api_key,
        "bgn_de": (today - timedelta(days=lookback_days)).strftime("%Y%m%d"),
        "end_de": today.strftime("%Y%m%d"),
        "page_count": 100,
        "page_no": 1,
    }

    items: dict[str, SourceItem] = {}
    for page in range(1, max_pages + 1):
        params["page_no"] = page
        resp = requests.get(LIST_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")
        if status == "013":  # 조회된 데이터 없음 → 정상
            break
        if status != "000":
            raise RuntimeError(f"OpenDART error {status}: {data.get('message')}")

        for row in data.get("list", []):
            item = to_item(row)
            if item:
                items[item.url] = item

        if page >= int(data.get("total_page", 1)):
            break

    return sorted(items.values(), key=lambda i: i.score, reverse=True)
