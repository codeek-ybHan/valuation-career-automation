"""Persist analysis and delivery stages together; retry only unfinished stages.

Local stage keys prevent repeats after an acknowledged success. A remote write
accepted just before a timeout/crash can still be repeated (at-least-once).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from models.valuation import SourceItem, ValuationAnalysis
from repositories.processed_repository import ProcessedRepository, _hash_for


class DeliveryRepository(ProcessedRepository):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            stage_key TEXT PRIMARY KEY, workflow TEXT NOT NULL, channel TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
        )""")
        self.conn.commit()

    def enqueue(self, workflow: str, pairs: list[tuple[SourceItem, ValuationAnalysis]],
                subject: str, html: str, *, notion: bool, gmail: bool) -> None:
        if not pairs:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            for item, analysis in pairs:
                payload = analysis.model_dump_json()
                if notion:
                    channels = ['notion_intelligence']
                    if analysis.interview_worthy:
                        channels.append('notion_interview')
                    for channel in channels:
                        self._insert(f'{channel}:{item.url}', workflow, channel, payload)
                self.conn.execute(
                    'INSERT OR IGNORE INTO processed_items (url,title_hash,source,processed_at) VALUES (?,?,?,?)',
                    (item.url, _hash_for(item), item.kind, now))
            if gmail:
                batch = hashlib.sha256(json.dumps(sorted(a.source_url for _, a in pairs)).encode()).hexdigest()
                self._insert(f'gmail:{workflow}:{batch}', workflow, 'gmail',
                             json.dumps({'subject': subject, 'html': html}, ensure_ascii=False))

    def _insert(self, key, workflow, channel, payload):
        self.conn.execute(
            'INSERT OR IGNORE INTO deliveries (stage_key,workflow,channel,payload) VALUES (?,?,?,?)',
            (key, workflow, channel, payload))

    def pending(self, workflow):
        return self.conn.execute(
            "SELECT stage_key,channel,payload FROM deliveries WHERE workflow=? AND state!='sent' ORDER BY rowid",
            (workflow,)).fetchall()

    def finish_attempt(self, key, error: str | None = None):
        with self.conn:
            self.conn.execute(
                'UPDATE deliveries SET state=?,attempts=attempts+1,last_error=? WHERE stage_key=?',
                ('failed' if error else 'sent', error, key))

    def flush(self, workflow, notion, gmail):
        failures = []
        for key, channel, payload in self.pending(workflow):
            try:
                if channel == 'gmail':
                    if gmail is None:
                        raise RuntimeError('Gmail is not configured for pending deliveries')
                    data = json.loads(payload)
                    gmail.send(data['subject'], data['html'])
                else:
                    if notion is None:
                        raise RuntimeError('Notion is not configured for pending deliveries')
                    analysis = ValuationAnalysis.model_validate_json(payload)
                    if channel == 'notion_intelligence':
                        notion.save_intelligence(analysis)
                    else:
                        notion.save_interview(analysis)
            except Exception as exc:
                # Store exception class only; provider messages may contain credentials.
                self.finish_attempt(key, type(exc).__name__)
                failures.append(channel)
            else:
                self.finish_attempt(key)
        if failures:
            raise RuntimeError(f'Delivery stages failed: {", ".join(failures)}')
