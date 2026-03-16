"""
Orchestrate the full router flow: classify report type -> check KB -> validate input -> return result.
Use this from the Streamlit app or API; missing_fields can be shown to prompt the user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Union

from loguru import logger

from router_agent.agent import classify_report_type
from router_agent.kb_client import get_required_field_paths, report_type_exists
from router_agent.schema_validator import (
    get_missing_required_fields,
    normalize_input_to_single_case,
)


@dataclass
class RouterResult:
    """Result of the router agent run."""

    report_type: str
    kb_status: str  # "EXISTS" | "MISSING"
    validated_input: Dict[str, Any]
    missing_fields: List[str] = field(default_factory=list)
    message: str = ""
    confidence_score: float = 0.0
    reasoning: str = ""
    # For pipeline compatibility
    report_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_type": self.report_type,
            "report_types": self.report_types,
            "kb_status": self.kb_status,
            "validated_input": self.validated_input,
            "missing_fields": self.missing_fields,
            "message": self.message,
            "confidence_score": self.confidence_score,
            "reasoning": self.reasoning,
        }


def _normalize_report_type(rt: str) -> str:
    rt = (rt or "").strip().upper()
    if rt in ("SAR", "CTR", "SANCTIONS", "BOTH"):
        return rt
    if rt == "OTHER":
        return "SAR"  # Default to SAR for "other" suspicious activity
    return rt


def run_router(
    user_input: Union[str, Dict[str, Any]],
    *,
    skip_llm_if_json_with_report_type: bool = True,
) -> RouterResult:
    """
    Run the router agent on user input (natural language or nested JSON).

    1. LLM classifies report type (or use report_type from JSON if present and allowed).
    2. Check Supabase KB for that report type (kb_status EXISTS | MISSING).
    3. If EXISTS: get JSON schema, validate user input against required fields.
    4. If any required field is missing, return them in missing_fields so the UI can prompt the user.

    Returns RouterResult with report_type, kb_status, validated_input, missing_fields, message.
    """
    # --- 1. Get report type ---
    if isinstance(user_input, dict) and skip_llm_if_json_with_report_type:
        hint = (user_input.get("report_type") or user_input.get("report_types"))
        if hint:
            if isinstance(hint, list):
                report_type = hint[0] if hint else "SAR"
            else:
                report_type = str(hint).upper()
            if report_type in ("SAR", "CTR", "SANCTIONS", "BOTH"):
                classification = {
                    "report_type": report_type,
                    "confidence_score": 1.0,
                    "reasoning": "Taken from input report_type.",
                }
            else:
                classification = classify_report_type(user_input)
        else:
            classification = classify_report_type(user_input)
    else:
        classification = classify_report_type(user_input)

    report_type = _normalize_report_type(classification.get("report_type", "SAR"))
    confidence_score = float(classification.get("confidence_score", 0.0))
    reasoning = str(classification.get("reasoning", ""))

    # For pipeline: report_types list (BOTH => ["CTR","SAR"] etc.)
    if report_type == "BOTH":
        report_types = ["CTR", "SAR"]
    elif report_type in ("SAR", "CTR", "SANCTIONS"):
        report_types = [report_type]
    else:
        report_types = [report_type] if report_type else ["SAR"]

    # --- 2. Check KB (for BOTH we check SAR as primary) ---
    schema_lookup_type = "SAR" if report_type == "BOTH" else report_type
    if not report_type_exists(schema_lookup_type):
        return RouterResult(
            report_type=report_type,
            report_types=report_types,
            kb_status="MISSING",
            validated_input=normalize_input_to_single_case(user_input) if isinstance(user_input, (dict, list)) else {},
            missing_fields=[],
            message=f"Report type '{report_type}' is not present in the Knowledge Base. Please add it to Supabase report_types or choose another report type.",
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

    # --- 3. Get required fields (from Supabase required_fields table or schema) ---
    try:
        required_paths = get_required_field_paths(schema_lookup_type)
    except Exception as e:
        logger.error("Failed to get required fields for %s: %s", report_type, e)
        return RouterResult(
            report_type=report_type,
            report_types=report_types,
            kb_status="EXISTS",
            validated_input=normalize_input_to_single_case(user_input) if isinstance(user_input, (dict, list)) else {},
            missing_fields=[],
            message=f"Could not load required fields for '{report_type}': {e}.",
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

    # --- 4. Validate input ---
    if isinstance(user_input, dict):
        case_data = normalize_input_to_single_case(user_input)
    elif isinstance(user_input, list):
        case_data = normalize_input_to_single_case(user_input)
    else:
        # Natural language: we have no structured data to validate; ask user to provide required fields
        case_data = {}
        missing_fields = required_paths
        message = (
            f"Report type '{report_type}' is in the Knowledge Base. "
            f"To proceed, please provide the following required information: {', '.join(required_paths)}. "
            "You can use the manual entry form or upload a JSON file with these fields."
        )
        return RouterResult(
            report_type=report_type,
            report_types=report_types,
            kb_status="EXISTS",
            validated_input=case_data,
            missing_fields=missing_fields,
            message=message,
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

    missing_fields = get_missing_required_fields(case_data, required_paths)

    if missing_fields:
        message = (
            f"Report type '{report_type}' is supported. The following required fields are missing or empty: {', '.join(missing_fields)}. "
            "Please provide these details before submitting to the pipeline."
        )
    else:
        message = f"Report type '{report_type}' confirmed. All required fields are present. Ready for the rest of the pipeline."

    return RouterResult(
        report_type=report_type,
        report_types=report_types,
        kb_status="EXISTS",
        validated_input=case_data,
        missing_fields=missing_fields,
        message=message,
        confidence_score=confidence_score,
        reasoning=reasoning,
    )
