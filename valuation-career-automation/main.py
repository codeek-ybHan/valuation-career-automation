"""Valuation Career Automation 진입점.

    python main.py daily   [--dry-run]   # 평일 아침: 뉴스+공시 Daily Brief
    python main.py big4    [--dry-run]   # 금요일: Big4 Weekly
    python main.py review  [--dry-run]   # 일요일: Weekly Interview Review
    python main.py sample  [--dry-run]   # 샘플 테스트 (SK하이닉스 신규시설투자 1건)

--dry-run : Notion 저장과 Gmail 발송, 처리 이력 기록은 하지 않고 output/에 HTML만 저장한다.

설계 원칙: 각 단계(수집 → 분석 → Notion → Gmail)를 try/except로 격리해서
한 단계가 실패해도 가능한 다음 단계는 계속 진행한다. (서비스 간 결합도 낮추기)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from collectors.big4 import collect_big4
from collectors.dart import collect_dart_disclosures
from collectors.news import collect_news
from config import KST, Settings, get_settings
from models.valuation import SourceItem, ValuationAnalysis
from repositories.processed_repository import ProcessedRepository
from services.gmail import GmailService, build_big4_email, build_daily_email, build_review_email
from services.llm import LLMService
from services.notion import NotionService

log = logging.getLogger("valuation")


# ---------- 공통 단계 ----------

def analyze_items(items: list[SourceItem], llm: LLMService, repo: ProcessedRepository | None
                  ) -> list[ValuationAnalysis]:
    """항목별로 분석. 한 항목이 실패해도 나머지는 계속한다.
    분석에 성공한 항목만 '처리됨'으로 기록 → 실패 항목은 다음 실행에서 다시 시도된다."""
    results: list[ValuationAnalysis] = []
    for item in items:
        try:
            analysis = llm.analyze(item)
        except Exception as e:
            log.error("Analysis failed for %s: %s", item.url, e)
            continue
        results.append(analysis)
        if repo:
            repo.mark_as_processed(item)
    return results


def save_to_notion(analyses: list[ValuationAnalysis], notion: NotionService | None) -> None:
    """Intelligence DB 저장 + 면접 가치가 높으면 Interview Bank에도 저장."""
    if not notion:
        log.info("Notion skipped")
        return
    saved = 0
    for a in analyses:
        try:
            notion.save_intelligence(a)
            saved += 1
        except Exception as e:
            log.error("Notion API failed (intelligence) for %s: %s", a.title, e)
        if a.interview_worthy:
            try:
                notion.save_interview(a)
            except Exception as e:
                log.error("Notion API failed (interview bank) for %s: %s", a.title, e)
    log.info("Saved to Notion: %d/%d", saved, len(analyses))


def deliver_email(subject: str, html: str, settings: Settings, gmail: GmailService | None,
                  tag: str) -> None:
    """HTML은 항상 output/에 남긴다 (Gmail 실패 시에도 결과를 잃지 않도록)."""
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"{tag}_{datetime.now(KST):%Y%m%d}.html"
    path.write_text(html, encoding="utf-8")
    log.info("Email HTML saved: %s", path)

    if not gmail:
        log.info("Gmail skipped")
        return
    try:
        gmail.send(subject, html)
        log.info("Email sent successfully")
    except Exception as e:
        log.error("Gmail send failed: %s", e)


def _optional(factory, name: str, dry_run: bool):
    """서비스 설정이 없거나 dry-run이면 None. 설정 누락이 전체 실행을 막지 않게 한다."""
    if dry_run:
        return None
    try:
        return factory()
    except RuntimeError as e:
        log.warning("%s disabled: %s", name, e)
        return None


# ---------- 워크플로 ----------

def run_daily_brief(dry_run: bool = False) -> None:
    settings = get_settings()
    llm = LLMService(settings)
    repo = None if dry_run else ProcessedRepository(settings.db_path)
    dedup_repo = repo or ProcessedRepository(settings.db_path)  # dry-run도 중복 판정은 읽기 전용으로 사용
    notion = _optional(lambda: NotionService(settings), "Notion", dry_run)
    gmail = _optional(lambda: GmailService(settings), "Gmail", dry_run)

    # 1) 수집 — 소스 하나가 실패해도 다른 소스는 진행
    news: list[SourceItem] = []
    try:
        log.info("Collecting news...")
        news = collect_news(settings.news_lookback_hours)
        log.info("Found %d articles", len(news))
    except Exception as e:
        log.error("News collection failed: %s", e)

    disclosures: list[SourceItem] = []
    if settings.dart_api_key:
        try:
            log.info("Collecting DART disclosures...")
            disclosures = collect_dart_disclosures(settings.dart_api_key, settings.dart_lookback_days)
            log.info("Found %d disclosures", len(disclosures))
        except Exception as e:
            log.error("DART collection failed: %s", e)
    else:
        log.info("DART_API_KEY not set - skipping disclosures")

    # 2) 중복 제거 후 상위 N건 선정 (이미 처리한 기사를 빼고 뽑아야 매일 새 3건이 나온다)
    fresh_news = dedup_repo.remove_duplicates(news)
    fresh_dart = dedup_repo.remove_duplicates(disclosures)
    log.info("Duplicate removed: %d", len(news) + len(disclosures) - len(fresh_news) - len(fresh_dart))
    selected = fresh_news[:settings.news_count] + fresh_dart[:settings.dart_count]
    log.info("Selected %d items (news %d, disclosures %d)", len(selected),
             min(len(fresh_news), settings.news_count), min(len(fresh_dart), settings.dart_count))
    if not selected:
        log.info("Nothing new to analyze. Done.")
        return

    # 3) 분석 → 4) Notion → 5) Gmail
    log.info("Running valuation analysis...")
    analyses = analyze_items(selected, llm, repo)
    if not analyses:
        log.error("All analyses failed")
        return
    save_to_notion(analyses, notion)
    subject, html = build_daily_email(analyses)
    deliver_email(subject, html, settings, gmail, "daily")


def run_big4_weekly(dry_run: bool = False) -> None:
    settings = get_settings()
    llm = LLMService(settings)
    repo = ProcessedRepository(settings.db_path)
    notion = _optional(lambda: NotionService(settings), "Notion", dry_run)
    gmail = _optional(lambda: GmailService(settings), "Gmail", dry_run)

    log.info("Collecting Big4 materials...")
    # 중복 제거 후에도 N건이 남도록 넉넉히 가져온다
    candidates = collect_big4(lookback_days=7, limit=settings.big4_count * 3)
    fresh = repo.remove_duplicates(candidates)[:settings.big4_count]
    log.info("Big4 candidates %d -> selected %d", len(candidates), len(fresh))
    if not fresh:
        log.info("No new Big4 material. Done.")
        return

    analyses = analyze_items(fresh, llm, None if dry_run else repo)
    if not analyses:
        log.error("All analyses failed")
        return
    save_to_notion(analyses, notion)
    subject, html = build_big4_email(analyses)
    deliver_email(subject, html, settings, gmail, "big4")


def run_weekly_review(dry_run: bool = False) -> None:
    settings = get_settings()
    llm = LLMService(settings)
    notion = NotionService(settings)  # 이 워크플로는 Notion 읽기가 입력이라 필수
    gmail = _optional(lambda: GmailService(settings), "Gmail", dry_run)

    log.info("Reading last 7 days from Notion...")
    records = notion.query_recent_intelligence(days=7)
    log.info("Found %d records", len(records))
    if not records:
        log.info("No records this week. Done.")
        return

    text = "\n".join(
        f"- [{r['type']}] {r['name']} | 회사: {r['company']} | 토픽: {', '.join(r['topics'])} | "
        f"중요도: {r['importance']} | 상태: {r['status']} | 면접소재: {'Y' if r['interview_worthy'] else 'N'}\n"
        f"  요약: {r['summary'][:300]}\n  공부 포인트: {r['study_point'][:200]}"
        for r in records
    )
    review = llm.weekly_review(text)
    subject, html = build_review_email(review)
    deliver_email(subject, html, settings, gmail, "review")


SAMPLE = SourceItem(
    title="SK하이닉스, 용인 반도체 클러스터 대규모 신규시설투자 결정",
    url="https://example.com/sample/skhynix-capex",
    kind="news",
    publisher="SAMPLE",
    content=(
        "제목: SK하이닉스, 용인 반도체 클러스터 대규모 신규시설투자 결정\n"
        "관련 내용: SK하이닉스가 HBM 등 차세대 메모리 생산능력 확대를 위해 신규 팹에 "
        "수조 원 규모의 시설투자를 결정했다. 투자는 수년에 걸쳐 집행되며 일부는 차입으로 조달할 "
        "예정이다. (※ 테스트용 샘플 자료)"
    ),
)


def run_sample(dry_run: bool = False) -> None:
    """기획서 27장: 기사 1건 → 분석 → (Notion) → (Gmail). 처리 이력은 남기지 않는다."""
    settings = get_settings()
    llm = LLMService(settings)
    notion = _optional(lambda: NotionService(settings), "Notion", dry_run)
    gmail = _optional(lambda: GmailService(settings), "Gmail", dry_run)

    analyses = analyze_items([SAMPLE], llm, None)
    for a in analyses:
        log.info("\n%s", a.model_dump_json(indent=2))
    save_to_notion(analyses, notion)
    if analyses:
        subject, html = build_daily_email(analyses)
        deliver_email(subject, html, settings, gmail, "sample")


COMMANDS = {
    "daily": run_daily_brief,
    "big4": run_big4_weekly,
    "review": run_weekly_review,
    "sample": run_sample,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valuation Career Automation")
    parser.add_argument("command", choices=COMMANDS, nargs="?", default="daily")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion/Gmail/처리이력 기록 없이 분석 결과 HTML만 output/에 저장")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        COMMANDS[args.command](dry_run=args.dry_run)
    except Exception as e:
        log.error("%s failed: %s", args.command, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
