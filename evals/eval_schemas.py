"""Evaluation boundary adapters. Original demo schemas remain frozen unchanged."""
from pydantic import field_validator
from .schemas import BillingAnalysis as FrozenBillingAnalysis
from .schemas import PlanRecommendation as FrozenPlanRecommendation, EmailCopy
from .normalization import money_value


class PlanRecommendation(FrozenPlanRecommendation):
    @field_validator('estimated_bill', 'savings', mode='before')
    @classmethod
    def accept_usd_presentation(cls, value):
        return money_value(value)


class BillingAnalysis(FrozenBillingAnalysis):
    recommended_plan: PlanRecommendation | None

    @field_validator('is_anomaly', 'needs_review', mode='before')
    @classmethod
    def require_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError('Expected a JSON boolean')
        return value
