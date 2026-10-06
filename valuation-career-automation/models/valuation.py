"""도메인 모델.

- SourceItem      : Collector가 만들어 내는 원자료 (뉴스/공시/Big4). 수집 단계의 출력.
- AnalysisOutput  : LLM이 채우는 필드만 모은 Structured Output 스키마.
- ValuationAnalysis: AnalysisOutput + 우리가 직접 붙이는 출처 정보.

source_url 같은 사실 정보는 LLM에게 생성시키지 않고 SourceItem에서 그대로 복사한다.
(기획서 25-4: 원문 URL 추적, LLM이 만든 사실만으로 판단하지 않기)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["news", "dart", "big4"]

Topic = Literal[
    "DCF", "WACC", "FCF", "CAPEX", "PPA", "Impairment",
    "Fair Value", "Comparable", "M&A", "AI/업무혁신",
]
Importance = Literal["high", "medium", "low"]


class SourceItem(BaseModel):
    """수집된 원자료 1건. Collector의 공통 출력 형식."""

    title: str
    url: str
    kind: SourceKind
    publisher: str | None = None
    published_at: datetime | None = None
    content: str = ""          # LLM에 넘길 본문/요약/공시 메타데이터
    company: str | None = None  # 공시처럼 회사가 명확한 경우
    score: float = 0.0          # 키워드 기반 우선순위 점수


class AnalysisOutput(BaseModel):
    """LLM Structured Output (일간 뉴스/공시용)."""

    title: str
    company: str | None
    summary: str
    why_valuation_matters: str
    valuation_topics: list[Topic]
    valuation_impact: str
    study_point: str
    quiz_question: str
    quiz_answer: str
    interview_worthy: bool
    interview_question: str | None
    interview_material: str | None
    importance: Importance


class Big4Output(AnalysisOutput):
    """Big4 주간 자료용: 일간 분석 + 직무/ValuFlow 연결."""

    job_connection: str
    valuflow_idea: str


class ValuationAnalysis(AnalysisOutput):
    """분석 결과 + 출처. Notion/Gmail 단계에서 사용하는 최종 형태."""

    source_url: str
    source_kind: SourceKind
    publisher: str | None = None
    published_at: datetime | None = None
    job_connection: str | None = None
    valuflow_idea: str | None = None

    @classmethod
    def from_llm(cls, out: AnalysisOutput, item: SourceItem) -> "ValuationAnalysis":
        data = out.model_dump()
        # 회사명이 명확한 소스(공시)는 LLM 추측보다 원본 값을 우선한다.
        if item.company:
            data["company"] = item.company
        return cls(
            **data,
            source_url=item.url,
            source_kind=item.kind,
            publisher=item.publisher,
            published_at=item.published_at,
        )


# ---- Weekly Interview Review ----

class DealPick(BaseModel):
    deal: str
    why_remember: str


class ConceptNote(BaseModel):
    concept: str
    explanation: str


class InterviewCase(BaseModel):
    case: str
    how_to_use: str


class WeeklyReview(BaseModel):
    top_deals: list[DealPick] = Field(description="이번 주 기억해야 할 Deal TOP 3")
    key_concepts: list[ConceptNote] = Field(description="이번 주 Valuation 핵심 개념 5개")
    review_concepts: list[ConceptNote] = Field(description="다시 공부해야 할 개념")
    interview_cases: list[InterviewCase] = Field(description="면접 활용 사례 TOP 3")
    expected_questions: list[str] = Field(description="예상 면접 질문 5개")
