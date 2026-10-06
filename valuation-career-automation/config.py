"""환경변수 로딩.

모든 비밀값은 환경변수(로컬은 .env, 운영은 GitHub Secrets)로만 읽는다.
서비스별로 필요한 값이 다르므로, 값이 없어도 import 시점에는 실패하지 않고
실제로 그 서비스를 쓰는 시점에 `require()`로 검증한다.
(예: OpenAI 키만 있어도 --dry-run으로 분석 단계까지 먼저 돌려볼 수 있다.)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    dart_api_key: str = field(default_factory=lambda: os.getenv("DART_API_KEY", ""))

    notion_token: str = field(default_factory=lambda: os.getenv("NOTION_TOKEN", ""))
    notion_intelligence_db_id: str = field(default_factory=lambda: os.getenv("NOTION_INTELLIGENCE_DB_ID", ""))
    notion_interview_db_id: str = field(default_factory=lambda: os.getenv("NOTION_INTERVIEW_DB_ID", ""))

    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_refresh_token: str = field(default_factory=lambda: os.getenv("GOOGLE_REFRESH_TOKEN", ""))
    email_to: str = field(default_factory=lambda: os.getenv("EMAIL_TO", ""))

    news_count: int = field(default_factory=lambda: _int("NEWS_COUNT", 3))
    dart_count: int = field(default_factory=lambda: _int("DART_COUNT", 3))
    big4_count: int = field(default_factory=lambda: _int("BIG4_COUNT", 5))
    news_lookback_hours: int = field(default_factory=lambda: _int("NEWS_LOOKBACK_HOURS", 24))
    dart_lookback_days: int = field(default_factory=lambda: _int("DART_LOOKBACK_DAYS", 1))

    llm_max_retry: int = 3
    db_path: Path = BASE_DIR / "data" / "processed.db"
    output_dir: Path = BASE_DIR / "output"


def get_settings() -> Settings:
    return Settings()


def require(settings: Settings, *names: str) -> None:
    """서비스 사용 직전에 필요한 값이 비어 있으면 명확한 에러를 낸다."""
    missing = [n for n in names if not getattr(settings, n.lower())]
    if missing:
        raise RuntimeError(f"환경변수가 설정되지 않았습니다: {', '.join(m.upper() for m in missing)}")
