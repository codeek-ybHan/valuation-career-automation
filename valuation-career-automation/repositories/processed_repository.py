"""처리 완료 항목 저장소 (SQLite).

같은 기사가 매일 다시 분석되는 것을 막는다.
- URL이 같으면 중복
- URL이 달라도 '정규화한 제목 해시'가 같으면 중복 (통신사 기사 재배포 대응)
단, 공시는 같은 회사가 같은 이름의 공시를 반복해서 내므로 제목 해시를 쓰지 않고 URL(접수번호)만 쓴다.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models.valuation import SourceItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL UNIQUE,
    title_hash   TEXT,
    source       TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_title_hash ON processed_items(title_hash);
"""


def title_hash(title: str) -> str:
    """공백/기호/대소문자 차이를 무시한 제목 해시."""
    normalized = re.sub(r"[^0-9a-z가-힣]", "", title.lower())
    return hashlib.sha1(normalized.encode()).hexdigest()


def _hash_for(item: SourceItem) -> str | None:
    return None if item.kind == "dart" else title_hash(item.title)


class ProcessedRepository:
    def __init__(self, db_path: Path | str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.executescript(SCHEMA)

    def is_processed(self, item: SourceItem) -> bool:
        h = _hash_for(item)
        row = self.conn.execute(
            "SELECT 1 FROM processed_items WHERE url = ? OR (title_hash IS NOT NULL AND title_hash = ?) LIMIT 1",
            (item.url, h),
        ).fetchone()
        return row is not None

    def mark_as_processed(self, item: SourceItem) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_items (url, title_hash, source, processed_at) VALUES (?, ?, ?, ?)",
            (item.url, _hash_for(item), item.kind, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def remove_duplicates(self, items: list[SourceItem]) -> list[SourceItem]:
        """DB에 있는 항목 + 이번 실행 내부 중복을 제거. 입력 순서(우선순위)는 유지."""
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        fresh: list[SourceItem] = []
        for item in items:
            h = _hash_for(item)
            if item.url in seen_urls or (h and h in seen_hashes) or self.is_processed(item):
                continue
            seen_urls.add(item.url)
            if h:
                seen_hashes.add(h)
            fresh.append(item)
        return fresh

    def close(self) -> None:
        self.conn.close()
