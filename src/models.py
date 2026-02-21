from pydantic import BaseModel, Field
from typing import List, Literal

class ValidationReport(BaseModel):
    status: Literal["APPROVED", "NEEDS_REVIEW", "REJECTED"] = Field(
        ..., description="Final decision based on checks."
    )
    completeness_score: float = Field(
        ..., description="Calculated completeness percentage (0-100%)."
    )
    missing_fields: List[str] = Field(
        default_factory=list, description="Any critically missing JSON keys."
    )
    compliance_issues: List[str] = Field(
        default_factory=list, description="Violations of validation rules or legal frameworks."
    )
    recommendations: List[str] = Field(
        default_factory=list, description="Actionable steps to resolve flags."
    )

class ValidatorOutput(BaseModel):
    validation_report: ValidationReport
    approval_flag: bool = Field(
        ..., description="True if status is APPROVED, False otherwise."
    )