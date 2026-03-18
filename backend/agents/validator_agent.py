"""Agent 5: Validator adapter wired to teammate validator package.

The validator package is sourced only from:
`Agent 4 - Narrative generator`
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VALIDATOR_CONTAINER_ROOT = _PROJECT_ROOT / "Agent 4 - Narrative generator"

_LOADED_COMPONENTS: Optional[Dict[str, Any]] = None


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_validator_package_root(root: Path) -> bool:
    return (
        (root / "utils" / "sar_rules_engine.py").exists()
        and (root / "utils" / "ctr_rules_engine.py").exists()
        and (root / "utils" / "scoring.py").exists()
        and (root / "data" / "validation_data" / "validation_rules.json").exists()
    )


def _discover_validator_root() -> Path:
    if not _VALIDATOR_CONTAINER_ROOT.exists():
        raise FileNotFoundError(
            f"Validator container folder not found: {_VALIDATOR_CONTAINER_ROOT}"
        )

    # Accept either a direct package drop-in at the root, or a nested validator folder.
    direct_candidates = [_VALIDATOR_CONTAINER_ROOT]
    direct_candidates.extend(
        [path for path in _VALIDATOR_CONTAINER_ROOT.iterdir() if path.is_dir()]
    )
    for candidate in direct_candidates:
        if _is_validator_package_root(candidate):
            return candidate

    # Deep-scan for validator files in case teammate used a custom nested layout.
    for sar_rules in _VALIDATOR_CONTAINER_ROOT.rglob("sar_rules_engine.py"):
        candidate = sar_rules.parent.parent
        if _is_validator_package_root(candidate):
            return candidate

    raise FileNotFoundError(
        "Teammate validator package was not found under "
        f"{_VALIDATOR_CONTAINER_ROOT}. Required files: "
        "utils/sar_rules_engine.py, utils/ctr_rules_engine.py, "
        "utils/scoring.py, data/validation_data/validation_rules.json"
    )


def _load_components() -> Dict[str, Any]:
    global _LOADED_COMPONENTS
    if _LOADED_COMPONENTS is not None:
        return _LOADED_COMPONENTS

    root = _discover_validator_root()
    sar_mod = _load_module("teammate_sar_rules_engine", root / "utils" / "sar_rules_engine.py")
    ctr_mod = _load_module("teammate_ctr_rules_engine", root / "utils" / "ctr_rules_engine.py")
    scoring_mod = _load_module("teammate_scoring", root / "utils" / "scoring.py")

    llm_path = root / "utils" / "llm_evaluator.py"
    llm_mod = _load_module("teammate_llm_evaluator", llm_path) if llm_path.exists() else None

    rules = json.loads((root / "data" / "validation_data" / "validation_rules.json").read_text(encoding="utf-8"))

    _LOADED_COMPONENTS = {
        "root": root,
        "SARRuleChecker": sar_mod.SARRuleChecker,
        "CTRRuleChecker": ctr_mod.CTRRuleChecker,
        "calculate_score": scoring_mod.calculate_score,
        "categorize_rule": scoring_mod.categorize_rule,
        "evaluate_narrative": getattr(llm_mod, "evaluate_narrative", None),
        "rules": rules,
    }
    logger.info("Validator adapter using teammate package at {}", root)
    return _LOADED_COMPONENTS


def _split_name(full_name: str) -> Tuple[str, str, str]:
    cleaned = " ".join((full_name or "").split())
    if not cleaned:
        return "", "", ""
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], parts[0], ""
    if len(parts) == 2:
        return parts[1], parts[0], ""
    return parts[-1], parts[0], " ".join(parts[1:-1])


def _first_location_city_state(transactions: List[Dict[str, Any]]) -> Tuple[str, str]:
    for tx in transactions:
        location = str(tx.get("location") or "").strip()
        if "," in location:
            city, state = location.split(",", 1)
            return city.strip(), state.strip()[:2]
    return "", ""


def _compose_address(address: str, city: str, state: str, zip_code: str) -> str:
    addr = (address or "").strip()
    if addr:
        return addr
    city = (city or "").strip()
    state = (state or "").strip()
    zip_code = (zip_code or "").strip()
    line = ", ".join(item for item in [city, state] if item)
    return (f"{line} {zip_code}").strip()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_date_yyyy_mm_dd(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text[:10]


def _build_sar_validator_input(
    normalized_case: Dict[str, Any],
    aggregator_output: Dict[str, Any],
    narrative_output: Dict[str, Any],
) -> Dict[str, Any]:
    case_subject = normalized_case.get("subject") if isinstance(normalized_case.get("subject"), dict) else {}
    agg_subject = aggregator_output.get("subject") if isinstance(aggregator_output.get("subject"), dict) else {}
    subject = {**dict(case_subject), **dict(agg_subject)}

    case_institution = normalized_case.get("institution") if isinstance(normalized_case.get("institution"), dict) else {}
    agg_institution = aggregator_output.get("institution") if isinstance(aggregator_output.get("institution"), dict) else {}
    agg_fin_inst = aggregator_output.get("financial_institution") if isinstance(aggregator_output.get("financial_institution"), dict) else {}
    institution = {**dict(case_institution), **dict(agg_institution)}

    transactions = normalized_case.get("transactions") if isinstance(normalized_case.get("transactions"), list) else []
    fallback_city, fallback_state = _first_location_city_state(transactions)

    name = str(subject.get("name") or aggregator_output.get("customer_name") or case_subject.get("name") or "").strip()
    last_name, first_name, middle_name = _split_name(name)

    subject_city = _first_non_empty(subject.get("city"), fallback_city, "UNKNOWN")
    subject_state = _first_non_empty(subject.get("state"), fallback_state, "NA")[:2]
    subject_zip = _first_non_empty(subject.get("zip"), subject.get("postal_code"), "00000")
    subject_tin = _first_non_empty(
        subject.get("tin"),
        subject.get("ssn_or_ein"),
        subject.get("ssn"),
        subject.get("ein"),
        subject.get("tax_id"),
        aggregator_output.get("customer_ssn"),
        "UNKNOWN",
    )
    subject_dob = _first_non_empty(
        subject.get("dob"),
        subject.get("date_of_birth"),
        aggregator_output.get("customer_dob"),
        "1900-01-01",
    )

    activity_range = aggregator_output.get("activity_date_range") if isinstance(aggregator_output.get("activity_date_range"), dict) else {}
    suspicious_block = (
        aggregator_output.get("SuspiciousActivityInformation")
        if isinstance(aggregator_output.get("SuspiciousActivityInformation"), dict)
        else {}
    )

    narrative_text = str(
        narrative_output.get("narrative_text")
        or narrative_output.get("narrative")
        or normalized_case.get("narrative")
        or ""
    ).strip()

    institution_tin = _first_non_empty(
        institution.get("tin"),
        institution.get("ein"),
        institution.get("ein_or_ssn"),
        agg_fin_inst.get("tin"),
        agg_fin_inst.get("ein"),
        agg_fin_inst.get("ein_or_ssn"),
        "UNKNOWN",
    )

    return {
        "report_type": "SAR",
        "case_id": str(aggregator_output.get("case_id") or normalized_case.get("case_id") or "UNKNOWN"),
        "filing_type": str(normalized_case.get("filing_type") or normalized_case.get("report_type") or "initial").lower(),
        "prior_report_number": normalized_case.get("prior_report_number"),
        "subject": {
            "last_name": str(subject.get("last_name") or last_name or name or "UNKNOWN"),
            "first_name": str(subject.get("first_name") or first_name or name),
            "middle_initial": str(subject.get("middle_initial") or middle_name[:1]),
            "occupation": str(subject.get("occupation") or subject.get("industry_or_occupation") or "Unknown"),
            "address": _compose_address(str(subject.get("address") or ""), subject_city, subject_state, subject_zip),
            "city": subject_city,
            "state": subject_state,
            "zip": subject_zip,
            "country": str(subject.get("country") or "US"),
            "tin": subject_tin,
            "dob": _to_date_yyyy_mm_dd(subject_dob),
        },
        "activity": {
            "amount": _as_float(aggregator_output.get("total_amount_involved")),
            "activity_date_range": {
                "start": _to_date_yyyy_mm_dd(activity_range.get("start") or suspicious_block.get("27_DateOrDateRange", {}).get("from")),
                "end": _to_date_yyyy_mm_dd(activity_range.get("end") or suspicious_block.get("27_DateOrDateRange", {}).get("to")),
            },
            "structuring": suspicious_block.get("29_Structuring", []) if isinstance(suspicious_block.get("29_Structuring"), list) else [],
            "terrorist_financing": suspicious_block.get("30_TerroristFinancing", []) if isinstance(suspicious_block.get("30_TerroristFinancing"), list) else [],
            "fraud": suspicious_block.get("31_Fraud", []) if isinstance(suspicious_block.get("31_Fraud"), list) else [],
            "money_laundering": suspicious_block.get("33_MoneyLaundering", []) if isinstance(suspicious_block.get("33_MoneyLaundering"), list) else [],
            "identification_issues": suspicious_block.get("34_IdentificationDocumentation", []) if isinstance(suspicious_block.get("34_IdentificationDocumentation"), list) else [],
            "other_suspicious": suspicious_block.get("35_OtherSuspiciousActivities", []) if isinstance(suspicious_block.get("35_OtherSuspiciousActivities"), list) else [],
        },
        "financial_institution": {
            "name": str(institution.get("name") or "Unknown Institution"),
            "tin": institution_tin,
        },
        "filing_institution": {
            "contact_office": str(institution.get("contact_officer") or "Compliance Office"),
            "contact_phone": str(institution.get("contact_phone") or ""),
            "date_filed": datetime.now().strftime("%Y-%m-%d"),
        },
        "narrative_required": bool(aggregator_output.get("narrative_required", True)),
        "narrative": narrative_text,
    }


def _build_ctr_validator_input(
    normalized_case: Dict[str, Any],
    aggregator_output: Dict[str, Any],
) -> Dict[str, Any]:
    subject = aggregator_output.get("subject") if isinstance(aggregator_output.get("subject"), dict) else {}
    institution = (
        aggregator_output.get("institution")
        if isinstance(aggregator_output.get("institution"), dict)
        else (normalized_case.get("institution") if isinstance(normalized_case.get("institution"), dict) else {})
    )
    transactions = normalized_case.get("transactions") if isinstance(normalized_case.get("transactions"), list) else []
    fallback_city, fallback_state = _first_location_city_state(transactions)

    name = str(subject.get("name") or normalized_case.get("subject", {}).get("name") or "").strip()
    last_name, first_name, middle_name = _split_name(name)

    subject_city = str(subject.get("city") or fallback_city or "").strip()
    subject_state = str(subject.get("state") or fallback_state or "").strip()[:2]
    subject_zip = str(subject.get("zip") or subject.get("postal_code") or "").strip()

    tx_block = aggregator_output.get("transaction") if isinstance(aggregator_output.get("transaction"), dict) else {}
    cash_in = _as_float(tx_block.get("cash_in"))
    cash_out = _as_float(tx_block.get("cash_out"))
    if cash_in == 0.0 and cash_out == 0.0:
        for tx in transactions:
            amount = _as_float(tx.get("amount") or tx.get("amount_usd"))
            tx_text = " ".join(
                [
                    str(tx.get("type") or ""),
                    str(tx.get("product_type") or ""),
                    str(tx.get("instrument_type") or ""),
                    str(tx.get("notes") or ""),
                ]
            ).lower()
            if "deposit" in tx_text or "cash" in tx_text:
                cash_in += amount
            else:
                cash_out += amount

    accounts = aggregator_output.get("account_numbers") if isinstance(aggregator_output.get("account_numbers"), list) else []

    return {
        "report_type": "CTR",
        "case_id": str(aggregator_output.get("case_id") or normalized_case.get("case_id") or "UNKNOWN"),
        "amends_prior": bool(normalized_case.get("amends_prior", False)),
        "multiple_persons": bool(normalized_case.get("multiple_persons", False)),
        "multiple_transactions": bool(normalized_case.get("multiple_transactions", False)),
        "section_a": {
            "last_name": str(subject.get("last_name") or last_name or name),
            "first_name": str(subject.get("first_name") or first_name or name),
            "middle_initial": str(subject.get("middle_initial") or middle_name[:1]),
            "ssn_or_ein": str(subject.get("tin") or subject.get("ssn") or subject.get("ein") or ""),
            "address": _compose_address(str(subject.get("address") or ""), subject_city, subject_state, subject_zip),
            "dob": str(subject.get("dob") or ""),
            "city": subject_city,
            "state": subject_state,
            "zip": subject_zip,
            "country": str(subject.get("country") or "US"),
            "occupation": str(subject.get("occupation") or subject.get("industry_or_occupation") or "Unknown"),
            "id_type": str(subject.get("id_type") or "drivers_license"),
            "id_issued_by": str(subject.get("id_issued_by") or subject_state or "US"),
            "id_number": str(subject.get("id_number") or "UNKNOWN"),
        },
        "section_b": normalized_case.get("section_b") if isinstance(normalized_case.get("section_b"), dict) else {"blank_reason": "conducted_on_own_behalf"},
        "transaction": {
            "cash_in": cash_in,
            "cash_out": cash_out,
            "date": _to_date_yyyy_mm_dd(tx_block.get("date") or datetime.now().strftime("%Y-%m-%d")),
            "currency_exchange": bool(tx_block.get("currency_exchange", False)),
            "wire_transfer": bool(tx_block.get("wire_transfer", False)),
            "account_numbers": accounts,
        },
        "institution": {
            "name": str(institution.get("name") or "Unknown Institution"),
            "regulator_code": str(institution.get("primary_federal_regulator") or institution.get("regulator_code") or ""),
            "address": str(institution.get("address") or _compose_address("", str(institution.get("branch_city") or fallback_city), str(institution.get("branch_state") or fallback_state), str(institution.get("zip") or ""))),
            "ein_or_ssn": str(institution.get("ein") or institution.get("tin") or ""),
            "city": str(institution.get("branch_city") or fallback_city),
            "state": str(institution.get("branch_state") or fallback_state),
            "zip": str(institution.get("zip") or ""),
        },
        "signature": {
            "contact_name": str(institution.get("contact_officer") or "Compliance Officer"),
            "contact_phone": str(institution.get("contact_phone") or ""),
            "date": datetime.now().strftime("%Y-%m-%d"),
        },
    }


def _build_recommendations(violations: List[Dict[str, Any]]) -> List[str]:
    recs: List[str] = []
    for violation in violations:
        message = str(violation.get("message") or "").strip()
        if not message:
            continue
        lowered = message.lower()
        if "missing" in lowered:
            recs.append(f"Complete missing field: {message}")
        elif "invalid" in lowered or "format" in lowered:
            recs.append(f"Correct format issue: {message}")
        else:
            recs.append(f"Review validation issue: {message}")
    if not recs:
        recs.append("No issues identified.")
    return sorted(set(recs))


def _build_report_text(
    violations: List[Dict[str, Any]],
    category_scores: Dict[str, float],
    report_type: str,
) -> str:
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("VALIDATION REPORT")
    lines.append("=" * 60)
    lines.append(f"Report Type: {report_type}")
    lines.append(f"Total Violations: {len(violations)}")
    lines.append("")
    lines.append("CATEGORY SCORES:")
    for key, value in category_scores.items():
        lines.append(f"  - {key.capitalize()}: {value}%")
    lines.append("")

    if violations:
        lines.append("VIOLATIONS:")
        for severity in ["critical", "high", "medium", "low"]:
            matching = [item for item in violations if str(item.get("severity", "")).lower() == severity]
            if not matching:
                continue
            lines.append(f"  {severity.upper()} ({len(matching)}):")
            for item in matching:
                lines.append(f"    - {item.get('rule_id')}: {item.get('message')}")
    else:
        lines.append("No violations found. Report is fully compliant.")
    lines.append("=" * 60)
    return "\n".join(lines)


def _prepare_llm_env_for_teammate() -> None:
    """Bridge OpenAI env vars/settings to teammate validator expected defaults."""
    from backend.config.settings import settings

    if not os.getenv("DEFAULT_LLM_API_KEY"):
        api_key = str(os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY or "").strip()
        if api_key:
            os.environ["DEFAULT_LLM_API_KEY"] = api_key

    if not os.getenv("DEFAULT_LLM_BASE_URL"):
        base_url = str(os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
        if base_url:
            os.environ["DEFAULT_LLM_BASE_URL"] = base_url.rstrip("/")

    if not os.getenv("DEFAULT_LLM_MODEL_NAME"):
        os.environ["DEFAULT_LLM_MODEL_NAME"] = str(os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()



def validate_with_teammate_agent(
    *,
    normalized_case: Dict[str, Any],
    aggregator_output: Dict[str, Any],
    narrative_output: Optional[Dict[str, Any]] = None,
    report_type: Optional[str] = None,
) -> Dict[str, Any]:
    components = _load_components()
    rules = [item for item in components["rules"] if item.get("report_type") == (report_type or aggregator_output.get("report_type") or "SAR").upper()]
    rtype = (report_type or aggregator_output.get("report_type") or "SAR").upper()

    if rtype == "SAR":
        payload = _build_sar_validator_input(normalized_case, aggregator_output, narrative_output or {})
        checker = components["SARRuleChecker"](payload, rules)
    elif rtype == "CTR":
        payload = _build_ctr_validator_input(normalized_case, aggregator_output)
        checker = components["CTRRuleChecker"](payload, rules)
    else:
        raise ValueError(f"Unsupported report_type for validation: {rtype}")

    violations = checker.check_all()

    narrative_score = None
    if rtype == "SAR" and payload.get("narrative"):
        evaluator = components.get("evaluate_narrative")
        if callable(evaluator):
            _prepare_llm_env_for_teammate()
            try:
                llm_result = evaluator(payload["narrative"])
            except Exception as exc:
                llm_result = {
                    "score": 50,
                    "missing_elements": ["LLM evaluation unavailable"],
                    "comments": str(exc),
                }
            narrative_score = float(llm_result.get("score", 50))
            for item in llm_result.get("missing_elements", []):
                violations.append(
                    {
                        "rule_id": "SAR-QUALITY-001",
                        "severity": "medium",
                        "message": f"Narrative missing element: {item}",
                    }
                )
            comment = str(llm_result.get("comments") or "").strip()
            if comment:
                violations.append(
                    {
                        "rule_id": "SAR-QUALITY-002",
                        "severity": "low",
                        "message": f"LLM comment: {comment}",
                    }
                )

    score_result = components["calculate_score"](violations, rules, rtype)
    category_scores = dict(score_result.get("scores", {}))
    if rtype == "SAR" and narrative_score is not None:
        category_scores["narrative"] = round(narrative_score, 2)
        score_result["scores"] = category_scores
        score_result["validation_score"] = round(sum(category_scores.values()) / max(len(category_scores), 1), 2)

    categorized: Dict[str, List[Dict[str, Any]]] = {
        "completeness": [],
        "compliance": [],
        "accuracy": [],
        "narrative": [],
    }
    categorize_rule = components.get("categorize_rule")
    for violation in violations:
        if callable(categorize_rule):
            category = str(categorize_rule(str(violation.get("rule_id", ""))))
        else:
            category = "completeness"
        categorized.setdefault(category, []).append(violation)

    completeness_score = float(category_scores.get("completeness", 0.0))
    compliance_score = float(category_scores.get("compliance", 0.0))
    narrative_quality = float(category_scores.get("narrative", 100.0 if rtype == "CTR" else 0.0))

    compliance_checks = {
        "required_fields": "PASS" if not categorized.get("completeness") else "FAIL",
        "bsa_compliance": "PASS" if not categorized.get("compliance") else "FAIL",
        "fincen_guidelines": "PASS" if not categorized.get("accuracy") else "FAIL",
    }

    output = {
        "status": score_result.get("status", "NEEDS_REVIEW"),
        "pass_or_not": score_result.get("pass_or_not", "No"),
        "validation_score": score_result.get("validation_score", 0.0),
        "scores": category_scores,
        "validation_report": _build_report_text(violations, category_scores, rtype),
        "violations": violations,
        "approval_flag": score_result.get("status", "").upper() == "APPROVED",
        "completeness_score": completeness_score,
        "compliance_checks": compliance_checks,
        "narrative_quality_score": narrative_quality,
        "narrative_quality_breakdown": {
            "clarity": round(narrative_quality, 2),
            "specificity": round(narrative_quality, 2),
            "completeness": round(narrative_quality, 2),
            "tone": round(narrative_quality, 2),
            "length": round(narrative_quality, 2),
            "regulatory_citation": round(narrative_quality, 2),
        },
        "issues": [str(item.get("message") or "") for item in violations],
        "recommendations": _build_recommendations(violations),
        "validator_source": str(components["root"]),
        "validator_input": payload,
    }
    return output
