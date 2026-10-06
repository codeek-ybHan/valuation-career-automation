"""외부 API 없이 도는 단위 테스트: 수집 파싱 / 공시 필터 / 중복 제거 / Notion 속성 / 메일 렌더링."""
from datetime import datetime, timedelta, timezone

from collectors.dart import to_item
from collectors.news import parse_rss, score_title
from models.valuation import SourceItem, ValuationAnalysis
from repositories.processed_repository import ProcessedRepository, title_hash
from services import notion
from services.gmail import build_daily_email, pick_quizzes

NOW = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate>"
        f"<description>&lt;a&gt;desc&lt;/a&gt;</description><source>{s}</source></item>"
        for t, l, d, s in items
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def make_analysis(**kw) -> ValuationAnalysis:
    base = dict(
        title="테스트 딜", company="A사", summary="요약", why_valuation_matters="중요",
        valuation_topics=["M&A", "DCF"], valuation_impact="영향", study_point="프리미엄은?",
        quiz_question="Q?", quiz_answer="A.", interview_worthy=True,
        interview_question="질문", interview_material="소재", importance="high",
        source_url="https://x.com/1", source_kind="news",
    )
    base.update(kw)
    return ValuationAnalysis(**base)


# ---- news ----

def test_parse_rss_filters_old_and_strips_publisher():
    xml = rss(
        ("A사 B사 인수 - 한경", "https://n/1", "Mon, 05 Oct 2026 20:00:00 GMT", "한경"),
        ("오래된 기사", "https://n/2", "Mon, 28 Sep 2026 20:00:00 GMT", "매경"),
    )
    items = parse_rss(xml, kind="news", lookback=timedelta(hours=24), now=NOW)
    assert [i.url for i in items] == ["https://n/1"]
    assert items[0].title == "A사 B사 인수"
    assert items[0].publisher == "한경"


def test_score_prefers_korean_and_keywords():
    assert score_title("삼성 M&A 인수") > score_title("weather today")
    assert score_title("삼성 M&A 인수", "한국경제") > score_title("삼성 M&A 인수")


# ---- dart ----

def test_dart_filter_keeps_only_valuation_relevant():
    keep = to_item({"corp_name": "X", "report_nm": "주요사항보고서(유상증자결정)",
                    "rcept_no": "20261005000001", "corp_cls": "Y", "rcept_dt": "20261005"})
    drop = to_item({"corp_name": "X", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
                    "rcept_no": "2", "corp_cls": "Y", "rcept_dt": "20261005"})
    assert keep is not None and "rcpNo=20261005000001" in keep.url
    assert drop is None


def test_dart_merger_outranks_buyback():
    merger = to_item({"corp_name": "A", "report_nm": "회사합병결정", "rcept_no": "1", "corp_cls": "K"})
    buyback = to_item({"corp_name": "A", "report_nm": "자기주식취득결정", "rcept_no": "2", "corp_cls": "K"})
    assert merger.score > buyback.score


# ---- dedup ----

def test_dedup_by_url_and_title_hash(tmp_path):
    repo = ProcessedRepository(tmp_path / "p.db")
    a = SourceItem(title="A사, B사 인수 합의", url="https://n/1", kind="news")
    repo.mark_as_processed(a)

    same_url = SourceItem(title="전혀 다른 제목", url="https://n/1", kind="news")
    same_title = SourceItem(title="A사 B사 인수 합의!", url="https://other/9", kind="news")
    fresh = SourceItem(title="새 기사", url="https://n/3", kind="news")
    dup_in_batch = SourceItem(title="새 기사", url="https://n/4", kind="news")

    result = repo.remove_duplicates([same_url, same_title, fresh, dup_in_batch])
    assert [i.url for i in result] == ["https://n/3"]


def test_dart_not_deduped_by_title(tmp_path):
    repo = ProcessedRepository(tmp_path / "p.db")
    d1 = SourceItem(title="[공시] A - 자기주식취득결정", url="https://dart/1", kind="dart")
    d2 = SourceItem(title="[공시] A - 자기주식취득결정", url="https://dart/2", kind="dart")
    repo.mark_as_processed(d1)
    assert repo.remove_duplicates([d2]) == [d2]


def test_title_hash_normalizes():
    assert title_hash("A사, B사 인수!") == title_hash("a사 b사 인수")


# ---- notion ----

def test_notion_properties_follow_spec():
    a = make_analysis()
    props = notion.intelligence_properties(a, "2026-10-06")
    assert props["Status"]["select"]["name"] == "안 봄"
    assert props["Importance"]["select"]["name"] == "★★★"
    assert props["Type"]["select"]["name"] == "M&A"
    assert props["Source"]["url"] == "https://x.com/1"

    ib = notion.interview_properties(a, "2026-10-06")
    assert ib["Ready"]["checkbox"] is False
    assert ib["Category"]["select"]["name"] == "최근 M&A"


def test_notion_rich_text_chunks_long_text():
    assert len(notion.rich_text("가" * 4500)["rich_text"]) == 3


# ---- email ----

def test_daily_email_renders_sections_and_omits_empty_disclosures():
    analyses = [make_analysis(), make_analysis(title="둘", importance="low", source_url="https://x.com/2")]
    subject, html = build_daily_email(analyses, now=datetime(2026, 10, 6))
    assert subject == "[Valuation Brief] 2026.10.06 | 오늘의 Deal · 공시 · Quiz"
    assert "오늘의 Valuation Quiz" in html and "https://x.com/1" in html
    assert "오늘의 공시" not in html  # 공시 없음 → 섹션 생략


def test_daily_email_escapes_html():
    _, html = build_daily_email([make_analysis(summary="<script>alert(1)</script>")])
    assert "<script>alert(1)" not in html


def test_pick_quizzes_prefers_high_importance_max_three():
    items = [make_analysis(importance=i, source_url=f"u{n}")
             for n, i in enumerate(["low", "high", "medium", "high"])]
    picked = pick_quizzes(items)
    assert len(picked) == 3 and picked[0].importance == "high"
