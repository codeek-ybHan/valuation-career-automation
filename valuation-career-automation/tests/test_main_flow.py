"""main.py 흐름 테스트: LLM/수집/Notion/Gmail을 가짜로 바꿔 '한 단계가 실패해도 계속 진행'을 검증."""
import main
from models.valuation import SourceItem, ValuationAnalysis
from tests.test_pipeline import make_analysis


class FakeLLM:
    def __init__(self, *_):
        pass

    def analyze(self, item):
        if "FAIL" in item.title:
            raise RuntimeError("boom")
        return make_analysis(title=item.title, source_url=item.url)


def setup(monkeypatch, tmp_path, items):
    settings = main.get_settings()
    object.__setattr__(settings, "db_path", tmp_path / "p.db")
    object.__setattr__(settings, "output_dir", tmp_path / "out")
    object.__setattr__(settings, "dart_api_key", "")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "LLMService", FakeLLM)
    monkeypatch.setattr(main, "collect_news", lambda hours: items)
    return settings


def test_daily_dry_run_skips_failed_item_and_writes_html(monkeypatch, tmp_path):
    items = [SourceItem(title=f"기사{i}", url=f"https://n/{i}", kind="news", score=5 - i) for i in range(3)]
    items.insert(1, SourceItem(title="FAIL 기사", url="https://n/fail", kind="news"))
    settings = setup(monkeypatch, tmp_path, items)

    main.run_daily_brief(dry_run=True)

    out = list(settings.output_dir.glob("daily_*.html"))
    assert len(out) == 1
    html = out[0].read_text(encoding="utf-8")
    assert "기사0" in html and "FAIL 기사" not in html


def test_gmail_failure_keeps_notion_and_html(monkeypatch, tmp_path):
    settings = setup(monkeypatch, tmp_path, [SourceItem(title="기사", url="https://n/1", kind="news", score=3)])
    saved = []

    class FakeNotion:
        def __init__(self, *_): pass
        def save_intelligence(self, a): saved.append(a.title)
        def save_interview(self, a): saved.append("IB:" + a.title)

    class BrokenGmail:
        def __init__(self, *_): pass
        def send(self, *_): raise RuntimeError("smtp down")

    monkeypatch.setattr(main, "NotionService", FakeNotion)
    monkeypatch.setattr(main, "GmailService", BrokenGmail)

    main.run_daily_brief(dry_run=False)

    assert saved == ["기사", "IB:기사"]                      # Notion은 저장됨 (면접 가치 → Bank도)
    assert list(settings.output_dir.glob("daily_*.html"))   # 메일 실패해도 HTML 보존

    # 다음 실행에서는 같은 기사가 중복으로 걸러진다
    saved.clear()
    main.run_daily_brief(dry_run=False)
    assert saved == []
