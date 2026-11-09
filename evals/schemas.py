"""Strict OpenAI Structured Outputs schemas. Frozen copy for the isolated evaluation module.

Install requirements.txt to inspect/validate these models locally; offline sample
mode does not need Pydantic or the SDK.
"""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

AnomalyType = Literal['none', 'constant_draw', 'baseline_step', 'peak_shift']
PeerStatus = Literal['above', 'below', 'typical', 'unavailable']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MetricJustification(StrictModel):
    metric_id: str = Field(description='An exact key from summary.metrics')
    calculated_value: str = Field(description='Copy the exact calculated metric value; never recalculate')


class PlanRecommendation(StrictModel):
    plan_id: str
    estimated_bill: Decimal = Field(description='Calculated cost of identical usage under this registered plan')
    savings: Decimal
    reason: str = Field(description='Short rationale; use metric placeholders for numeric claims')


class BillingAnalysis(StrictModel):
    is_anomaly: bool
    anomaly_type: AnomalyType
    mathematical_justification: list[MetricJustification] = Field(min_length=1)
    peer_comparison_status: PeerStatus
    recommended_plan: PlanRecommendation | None
    headline: str
    explanation: str
    actions: list[str] = Field(min_length=1, max_length=2)
    needs_review: bool


class EmailCopy(StrictModel):
    subject: str
    intro: str
    explanation: str
    actions: list[str] = Field(min_length=1, max_length=1)
