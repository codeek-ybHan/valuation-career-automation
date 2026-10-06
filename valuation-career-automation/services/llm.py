"""OpenAI Structured Output 호출.

`client.chat.completions.parse(response_format=<Pydantic 모델>)`을 쓰면 모델이 JSON Schema를
강제로 따르므로 문자열 파싱 없이 곧바로 Pydantic 객체를 받는다.
Literal로 정의한 topics/importance도 스키마 레벨에서 보장된다.
"""
from __future__ import annotations

import logging
import time
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config import BASE_DIR, Settings, require
from models.valuation import (
    AnalysisOutput, Big4Output, SourceItem, ValuationAnalysis, WeeklyReview,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SYSTEM_PROMPT = "You are a careful valuation research assistant. Respond only in the requested schema."


def _load_prompt(name: str) -> str:
    return (BASE_DIR / "prompts" / name).read_text(encoding="utf-8")


class LLMService:
    def __init__(self, settings: Settings):
        require(settings, "openai_api_key")
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _parse(self, prompt: str, schema: type[T]) -> T:
        """Structured Output 호출. 실패 시 지수 백오프로 최대 llm_max_retry회 재시도."""
        last_error: Exception | None = None
        for attempt in range(1, self.settings.llm_max_retry + 1):
            try:
                resp = self.client.chat.completions.parse(
                    model=self.settings.openai_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=schema,
                    temperature=0.3,
                )
                message = resp.choices[0].message
                if message.refusal:
                    raise RuntimeError(f"LLM refused: {message.refusal}")
                if message.parsed is None:
                    raise RuntimeError("LLM returned no parsed output")
                return message.parsed
            except Exception as e:
                last_error = e
                log.warning("LLM attempt %d/%d failed: %s", attempt, self.settings.llm_max_retry, e)
                if attempt < self.settings.llm_max_retry:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM failed after {self.settings.llm_max_retry} attempts: {last_error}")

    def analyze(self, item: SourceItem) -> ValuationAnalysis:
        """뉴스/공시/Big4 1건을 가치평가 관점으로 분석."""
        if item.kind == "big4":
            prompt = _load_prompt("weekly_big4.txt")
            schema: type[AnalysisOutput] = Big4Output
        else:
            prompt = _load_prompt("daily_analysis.txt")
            schema = AnalysisOutput

        # str.format 대신 replace: 프롬프트/기사 안의 중괄호가 충돌하지 않도록
        prompt = prompt.replace("{article}", item.content or item.title)
        out = self._parse(prompt, schema)

        analysis = ValuationAnalysis.from_llm(out, item)
        if isinstance(out, Big4Output):
            analysis.job_connection = out.job_connection
            analysis.valuflow_idea = out.valuflow_idea
        return analysis

    def weekly_review(self, records_text: str) -> WeeklyReview:
        prompt = _load_prompt("weekly_review.txt").replace("{records}", records_text)
        return self._parse(prompt, WeeklyReview)
