"""Delivery retries use persisted payloads without repeating successful stages."""
import pytest
from models.valuation import SourceItem
from repositories.delivery_repository import DeliveryRepository
from tests.test_pipeline import make_analysis


class Notion:
    def __init__(self, fail=False):
        self.fail = fail
        self.intelligence = 0
        self.interviews = 0
    def save_intelligence(self, analysis):
        if self.fail:
            raise RuntimeError('unavailable')
        self.intelligence += 1
    def save_interview(self, analysis):
        self.interviews += 1


class Gmail:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
    def send(self, subject, html):
        if self.fail:
            raise RuntimeError('unavailable')
        self.sent.append((subject, html))


def queue(repo):
    item = SourceItem(title='A', url='https://n/1', kind='news')
    repo.enqueue('daily', [(item, make_analysis(source_url=item.url))],
                 'original subject', '<p>saved analysis</p>', notion=True, gmail=True)
    return item


def test_gmail_retry_survives_restart_without_repeating_notion(tmp_path):
    path = tmp_path / 'queue.db'
    repo = DeliveryRepository(path)
    item = queue(repo)
    notion, gmail = Notion(), Gmail(fail=True)
    with pytest.raises(RuntimeError, match='gmail'):
        repo.flush('daily', notion, gmail)
    assert repo.is_processed(item)
    assert notion.intelligence == notion.interviews == 1
    repo.close()
    retry = DeliveryRepository(path)
    gmail.fail = False
    retry.flush('daily', notion, gmail)
    retry.flush('daily', notion, gmail)
    assert notion.intelligence == notion.interviews == 1
    assert gmail.sent == [('original subject', '<p>saved analysis</p>')]


def test_notion_retry_does_not_repeat_successful_email_or_interview(tmp_path):
    repo = DeliveryRepository(tmp_path / 'queue.db')
    queue(repo)
    notion, gmail = Notion(fail=True), Gmail()
    with pytest.raises(RuntimeError, match='notion_intelligence'):
        repo.flush('daily', notion, gmail)
    assert len(gmail.sent) == 1 and notion.interviews == 1
    notion.fail = False
    repo.flush('daily', notion, gmail)
    assert notion.intelligence == notion.interviews == 1
    assert len(gmail.sent) == 1


def test_repeated_enqueue_is_idempotent_and_legacy_rows_survive(tmp_path):
    repo = DeliveryRepository(tmp_path / 'queue.db')
    old = SourceItem(title='old', url='https://old', kind='news')
    repo.mark_as_processed(old)
    queue(repo)
    queue(repo)
    assert repo.is_processed(old)
    assert len(repo.pending('daily')) == 3
    assert repo.pending('big4') == []


def test_enqueue_and_processed_marker_roll_back_together(tmp_path, monkeypatch):
    repo = DeliveryRepository(tmp_path / 'queue.db')
    monkeypatch.setattr(repo, '_insert', lambda *args: (_ for _ in ()).throw(RuntimeError('db write failed')))
    with pytest.raises(RuntimeError):
        queue(repo)
    assert repo.conn.execute('SELECT count(*) FROM processed_items').fetchone()[0] == 0
