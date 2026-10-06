"""Notion 저장/조회.

공식 SDK 대신 requests로 REST API를 직접 호출한다. 쓰는 엔드포인트가
pages 생성, database query 두 개뿐이라 의존성을 늘릴 이유가 없다.

DB 속성 이름/타입은 README의 'Notion DB 구성'과 정확히 일치해야 한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from config import KST, Settings, require
from models.valuation import ValuationAnalysis

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
STARS = {"high": "★★★", "medium": "★★", "low": "★"}
TYPE_BY_KIND = {"news": "M&A", "dart": "공시", "big4": "Big4"}


# ---------- 속성 빌더 (순수 함수 → 테스트 용이) ----------

def rich_text(text: str | None) -> dict:
    """Notion rich_text 한 블록은 2000자 제한 → 나눠서 넣는다."""
    text = text or ""
    chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)] or [""]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks[:100]]}


def title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text[:2000]}}]}


def intelligence_properties(a: ValuationAnalysis, today: str) -> dict:
    summary = a.summary
    if a.job_connection or a.valuflow_idea:  # Big4는 직무/ValuFlow 연결도 요약에 포함
        summary += f"\n\n[직무 연결] {a.job_connection or '-'}\n[ValuFlow 아이디어] {a.valuflow_idea or '-'}"
    return {
        "Name": title(a.title),
        "Date": {"date": {"start": today}},
        "Type": {"select": {"name": TYPE_BY_KIND[a.source_kind]}},
        "Company": rich_text(a.company),
        "Topic": {"multi_select": [{"name": t} for t in a.valuation_topics]},
        "Source": {"url": a.source_url},
        "Summary": rich_text(summary),
        "Valuation Impact": rich_text(a.valuation_impact),
        "Study Point": rich_text(a.study_point),
        "Interview Worthy": {"checkbox": a.interview_worthy},
        "Importance": {"select": {"name": STARS[a.importance]}},
        "Status": {"select": {"name": "안 봄"}},  # 신규 데이터 기본값
    }


def interview_category(a: ValuationAnalysis) -> str:
    if a.source_kind == "news":
        return "최근 M&A"
    if "AI/업무혁신" in a.valuation_topics:
        return "AI/업무혁신"
    return "Valuation 이슈"


def interview_properties(a: ValuationAnalysis, today: str) -> dict:
    return {
        "Name": title(a.title),
        "Date": {"date": {"start": today}},
        "Category": {"select": {"name": interview_category(a)}},
        "Question": rich_text(a.interview_question),
        "My Answer Material": rich_text(a.interview_material),
        "Related Company": rich_text(a.company),
        "Related Concept": rich_text(", ".join(a.valuation_topics)),
        "Source": {"url": a.source_url},
        "Ready": {"checkbox": False},  # 직접 정리 완료했을 때만 사람이 true로 변경
    }


def _plain(prop: dict) -> str:
    """title / rich_text 속성 → 문자열."""
    parts = prop.get("title") or prop.get("rich_text") or []
    return "".join(p.get("plain_text", "") for p in parts)


def parse_intelligence_page(page: dict) -> dict:
    p = page["properties"]
    return {
        "name": _plain(p.get("Name", {})),
        "type": (p.get("Type", {}).get("select") or {}).get("name", ""),
        "company": _plain(p.get("Company", {})),
        "topics": [t["name"] for t in p.get("Topic", {}).get("multi_select", [])],
        "importance": (p.get("Importance", {}).get("select") or {}).get("name", ""),
        "status": (p.get("Status", {}).get("select") or {}).get("name", ""),
        "interview_worthy": p.get("Interview Worthy", {}).get("checkbox", False),
        "summary": _plain(p.get("Summary", {})),
        "study_point": _plain(p.get("Study Point", {})),
        "url": p.get("Source", {}).get("url") or "",
    }


# ---------- 서비스 ----------

class NotionService:
    def __init__(self, settings: Settings):
        require(settings, "notion_token", "notion_intelligence_db_id")
        self.settings = settings
        self.headers = {
            "Authorization": f"Bearer {settings.notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _create_page(self, database_id: str, properties: dict) -> str:
        resp = requests.post(
            f"{API}/pages", headers=self.headers, timeout=20,
            json={"parent": {"database_id": database_id}, "properties": properties},
        )
        if not resp.ok:
            raise RuntimeError(f"Notion {resp.status_code}: {resp.text[:300]}")
        return resp.json()["id"]

    @staticmethod
    def _today() -> str:
        return datetime.now(KST).strftime("%Y-%m-%d")

    def save_intelligence(self, a: ValuationAnalysis) -> str:
        return self._create_page(self.settings.notion_intelligence_db_id,
                                 intelligence_properties(a, self._today()))

    def save_interview(self, a: ValuationAnalysis) -> str:
        require(self.settings, "notion_interview_db_id")
        return self._create_page(self.settings.notion_interview_db_id,
                                 interview_properties(a, self._today()))

    def query_recent_intelligence(self, days: int = 7) -> list[dict]:
        """최근 N일 Valuation Intelligence 레코드 (Weekly Review 입력)."""
        since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
        pages: list[dict] = []
        cursor = None
        while True:
            body: dict = {
                "filter": {"property": "Date", "date": {"on_or_after": since}},
                "page_size": 100,
            }
            if cursor:
                body["start_cursor"] = cursor
            resp = requests.post(
                f"{API}/databases/{self.settings.notion_intelligence_db_id}/query",
                headers=self.headers, json=body, timeout=20,
            )
            if not resp.ok:
                raise RuntimeError(f"Notion {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            pages.extend(parse_intelligence_page(p) for p in data["results"])
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
        return pages
