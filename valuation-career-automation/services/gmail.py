"""Gmail 발송 + HTML 메일 렌더링.

비밀번호 대신 OAuth2 refresh token을 쓴다.
 1) refresh token → access token 교환 (oauth2.googleapis.com/token)
 2) Gmail API users.messages.send 에 base64url 인코딩한 MIME 메시지 전송
google-api-python-client 없이 requests만으로 충분해서 의존성을 줄였다.
refresh token 발급은 scripts/get_gmail_refresh_token.py 참고.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from email.message import EmailMessage

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import BASE_DIR, KST, Settings, require
from models.valuation import ValuationAnalysis, WeeklyReview

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

STARS = {"high": "★★★", "medium": "★★", "low": "★"}
IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2}

_env = Environment(
    loader=FileSystemLoader(BASE_DIR / "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["stars"] = lambda v: STARS.get(v, "")


# ---------- 메일 본문 생성 (순수 함수) ----------

def _date_label(now: datetime | None = None) -> str:
    return (now or datetime.now(KST)).strftime("%Y.%m.%d")


def pick_quizzes(analyses: list[ValuationAnalysis], n: int = 3) -> list[ValuationAnalysis]:
    """중요도 높은 순으로 퀴즈 n개 선정 (기획서 13: Daily Brief마다 3문제)."""
    ranked = sorted(analyses, key=lambda a: IMPORTANCE_RANK[a.importance])
    return ranked[:n]


def build_daily_email(analyses: list[ValuationAnalysis], now: datetime | None = None) -> tuple[str, str]:
    """(subject, html). 공시가 없으면 공시 섹션은 템플릿에서 자동 생략된다."""
    date = _date_label(now)
    html = _env.get_template("daily_email.html").render(
        date=date,
        news=[a for a in analyses if a.source_kind == "news"],
        disclosures=[a for a in analyses if a.source_kind == "dart"],
        quizzes=pick_quizzes(analyses),
        interviews=[a for a in analyses if a.interview_worthy],
        analyses=analyses,
    )
    return f"[Valuation Brief] {date} | 오늘의 Deal · 공시 · Quiz", html


def build_big4_email(analyses: list[ValuationAnalysis], now: datetime | None = None) -> tuple[str, str]:
    date = _date_label(now)
    html = _env.get_template("big4_email.html").render(date=date, items=analyses)
    return f"[Valuation Brief] {date} | Big4 Weekly", html


def build_review_email(review: WeeklyReview, now: datetime | None = None) -> tuple[str, str]:
    date = _date_label(now)
    html = _env.get_template("weekly_review_email.html").render(date=date, r=review)
    return f"[Valuation Interview Review] {date} | 이번 주 면접 복습", html


def _html_to_text(html: str) -> str:
    """HTML 미지원 클라이언트용 plain-text 대체 본문."""
    text = re.sub(r"<(style|script).*?</\1>", "", html, flags=re.S)
    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ---------- 발송 ----------

class GmailService:
    def __init__(self, settings: Settings):
        require(settings, "google_client_id", "google_client_secret",
                "google_refresh_token", "email_to")
        self.settings = settings

    def _access_token(self) -> str:
        resp = requests.post(TOKEN_URL, timeout=20, data={
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "refresh_token": self.settings.google_refresh_token,
            "grant_type": "refresh_token",
        })
        if not resp.ok:
            raise RuntimeError(f"Google token refresh failed {resp.status_code}: {resp.text[:200]}")
        return resp.json()["access_token"]

    def send(self, subject: str, html: str) -> None:
        msg = EmailMessage()
        msg["To"] = self.settings.email_to
        msg["Subject"] = subject
        msg.set_content(_html_to_text(html))
        msg.add_alternative(html, subtype="html")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = requests.post(
            SEND_URL, timeout=30, json={"raw": raw},
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        if not resp.ok:
            raise RuntimeError(f"Gmail send failed {resp.status_code}: {resp.text[:300]}")
