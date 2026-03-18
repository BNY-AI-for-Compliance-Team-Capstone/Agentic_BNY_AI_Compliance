from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

from crewai import Agent, LLM, Task
from loguru import logger

from backend.tools.field_mapper import calculate_total_cash_amount, determine_report_types, normalize_case_data

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEAMMATE_ROUTER_ROOT = _PROJECT_ROOT / "Agent 1  - Router Agent"


def _clear_router_agent_modules() -> None:
    for key in list(sys.modules.keys()):
        if key == "router_agent" or key.startswith("router_agent."):
            sys.modules.pop(key, None)


def _load_teammate_router_module() -> ModuleType:
    """Load teammate Router package from Agent 1 folder."""
    if not _TEAMMATE_ROUTER_ROOT.exists():
        raise FileNotFoundError(f"Teammate router folder not found: {_TEAMMATE_ROUTER_ROOT}")

    root_str = str(_TEAMMATE_ROUTER_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    existing = sys.modules.get("router_agent.run")
    existing_file = str(getattr(existing, "__file__", "")) if existing else ""
    if existing and root_str not in existing_file:
        _clear_router_agent_modules()

    return importlib.import_module("router_agent.run")


def _normalize_report_types(report_type: str, report_types: List[str] | None) -> List[str]:
    out: List[str] = []
    if isinstance(report_types, list):
        for item in report_types:
            code = str(item or "").upper()
            if code in {"SAR", "CTR"} and code not in out:
                out.append(code)

    if out:
        return out

    single = str(report_type or "").upper()
    if single == "BOTH":
        return ["CTR", "SAR"]
    if single in {"SAR", "CTR"}:
        return [single]
    return []


def _fallback_router_output(user_input: Any, error: str | None = None) -> Dict[str, Any]:
    case = normalize_case_data(user_input)
    report_types = determine_report_types(case)
    reasoning = "Fallback deterministic routing based on thresholds and suspicious activity indicators."
    if error:
        reasoning = f"{reasoning} Teammate router unavailable: {error}"

    return {
        "report_type": "BOTH" if len(report_types) > 1 else (report_types[0] if report_types else "NONE"),
        "report_types": report_types,
        "confidence_score": 1.0 if report_types else 0.0,
        "total_cash_amount": calculate_total_cash_amount(case),
        "reasoning": reasoning,
        "kb_status": "EXISTS",
        "narrative_description": "Router fallback classification completed.",
        "missing_fields": [],
        "missing_field_prompts": [],
        "message": "Router fallback was used.",
        "validated_input": case,
    }


def run_router_stage(user_input: str | Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute teammate Agent 1 router logic and return pipeline-compatible payload.

    Output contract is aligned with existing orchestration expectations and includes
    teammate-specific fields (missing_fields, prompts, validated_input, message).
    """
    try:
        module = _load_teammate_router_module()
        run_router = getattr(module, "run_router")
        result_obj = run_router(user_input)
        if hasattr(result_obj, "to_dict"):
            raw = result_obj.to_dict()
        elif isinstance(result_obj, dict):
            raw = result_obj
        else:
            raw = {}

        report_type = str(raw.get("report_type") or "OTHER").upper()
        report_types = _normalize_report_types(report_type, raw.get("report_types"))

        validated_input = raw.get("validated_input")
        if not isinstance(validated_input, dict):
            validated_input = normalize_case_data(user_input)
        else:
            validated_input = normalize_case_data(validated_input)

        kb_status = str(raw.get("kb_status") or "EXISTS").upper()
        if kb_status not in {"EXISTS", "MISSING"}:
            kb_status = "EXISTS"

        message = str(raw.get("message") or "").strip()
        reasoning = str(raw.get("reasoning") or "").strip()
        narrative_description = message or reasoning

        return {
            "report_type": report_type,
            "report_types": report_types,
            "confidence_score": float(raw.get("confidence_score") or 0.0),
            "total_cash_amount": calculate_total_cash_amount(validated_input),
            "reasoning": reasoning,
            "kb_status": kb_status,
            "narrative_description": narrative_description,
            "missing_fields": raw.get("missing_fields") if isinstance(raw.get("missing_fields"), list) else [],
            "missing_field_prompts": raw.get("missing_field_prompts") if isinstance(raw.get("missing_field_prompts"), list) else [],
            "message": message,
            "validated_input": validated_input,
            "raw_router": raw,
        }
    except Exception as exc:
        logger.warning("Teammate router failed; using fallback. Error: {}", exc)
        return _fallback_router_output(user_input, error=str(exc))


# Backward-compatible CrewAI constructors retained for callers that still use them.
def create_router_agent(llm: LLM, tools: list) -> Agent:
    return Agent(
        role="Compliance Report Router",
        goal="Determine which report(s) are required (SAR, CTR, or BOTH) based on transaction data",
        backstory=(
            "You are an expert compliance analyst who classifies filing requirements "
            "and validates whether required fields are present before downstream processing."
        ),
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=2,
    )


def create_router_task(agent: Agent, transaction_data: dict) -> Task:
    return Task(
        description=(
            "Analyze the transaction data and return JSON with report_types, confidence_score, "
            "reasoning, kb_status, and missing_fields.\n\n"
            f"Transaction Data:\n{json.dumps(transaction_data, indent=2)}"
        ),
        expected_output=(
            '{"report_types":["SAR"],"confidence_score":1.0,'
            '"reasoning":"...","kb_status":"EXISTS","missing_fields":[]}'
        ),
        agent=agent,
    )
