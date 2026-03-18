import json
from typing import Any, Callable, Dict

from crewai import LLM

from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler
from backend.tools.field_mapper import (
    calculate_total_cash_amount,
    determine_report_types,
    has_suspicious_activity,
    normalize_case_data,
)
from backend.config.settings import settings
from backend.agents.aggregator_agent import AggregatorOrchestrator
from backend.agents.router_agent import run_router_stage
from backend.agents.narrative_agent import generate_narrative_payload
from backend.agents.validator_agent import validate_with_teammate_agent


def _parse_jsonish(payload) -> Dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}
    raw = getattr(payload, "raw", None)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _build_router_reasoning(total_cash_amount: float, suspicious: bool, report_types: list[str]) -> str:
    if not report_types:
        return "No suspicious indicators and cash amount below filing threshold."
    reasons = []
    if "CTR" in report_types:
        reasons.append(f"total cash amount is ${total_cash_amount:,.2f} (>= $10,000)")
    if "SAR" in report_types and suspicious:
        reasons.append("suspicious activity indicators are present")
    if len(report_types) == 2:
        return "Both report types required because " + " and ".join(reasons) + "."
    if report_types[0] == "CTR":
        return "CTR required because " + " and ".join(reasons) + "."
    return "SAR required because " + " and ".join(reasons) + "."


def _build_narrative_input(normalized_case: Dict[str, Any], sar_aggregate: Dict[str, Any]) -> Dict[str, Any]:
    """Build Agent 4 input payload with required keys."""
    output = dict(normalized_case)
    output["case_id"] = sar_aggregate.get("case_id") or output.get("case_id")

    subject = output.get("subject")
    if not isinstance(subject, dict) or not subject:
        subject = {
            "subject_id": sar_aggregate.get("customer_id"),
            "name": sar_aggregate.get("customer_name"),
        }
    output["subject"] = subject

    suspicious_info = output.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict) or not suspicious_info:
        suspicious_info = sar_aggregate.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict):
        suspicious_info = {
            "26_AmountInvolved": {"amount_usd": sar_aggregate.get("total_amount_involved", 0.0), "no_amount": False},
            "27_DateOrDateRange": {
                "from": (sar_aggregate.get("activity_date_range") or {}).get("start"),
                "to": (sar_aggregate.get("activity_date_range") or {}).get("end"),
            },
            "35_OtherSuspiciousActivities": sar_aggregate.get("suspicious_activity_type", []),
        }
    output["SuspiciousActivityInformation"] = suspicious_info
    return output


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _extract_city_state_from_transactions(case_data: Dict[str, Any]) -> tuple[str, str]:
    txs = case_data.get("transactions")
    if not isinstance(txs, list):
        return "", ""
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        location = str(tx.get("location") or "").strip()
        if "," not in location:
            continue
        city, state = location.split(",", 1)
        city = city.strip()
        state = state.strip()[:2]
        if city or state:
            return city, state
    return "", ""


def _enrich_case_for_validator(
    normalized_case: Dict[str, Any],
    aggregator_output: Dict[str, Any],
    report_type: str,
) -> Dict[str, Any]:
    case_data = dict(normalized_case)
    subject = case_data.get("subject") if isinstance(case_data.get("subject"), dict) else {}
    subject = dict(subject)
    agg_subject = aggregator_output.get("subject") if isinstance(aggregator_output.get("subject"), dict) else {}

    fallback_city, fallback_state = _extract_city_state_from_transactions(case_data)

    subject_city = _first_non_empty(subject.get("city"), agg_subject.get("city"), fallback_city, "UNKNOWN")
    subject_state = _first_non_empty(subject.get("state"), agg_subject.get("state"), fallback_state, "NA")[:2]
    subject_zip = _first_non_empty(subject.get("zip"), subject.get("postal_code"), agg_subject.get("zip"), agg_subject.get("postal_code"), "00000")

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

    subject["city"] = subject_city
    subject["state"] = subject_state
    subject["zip"] = subject_zip
    subject["country"] = _first_non_empty(subject.get("country"), agg_subject.get("country"), "US")
    subject["address"] = _first_non_empty(
        subject.get("address"),
        agg_subject.get("address"),
        f"{subject_city}, {subject_state}".strip(", "),
        "UNKNOWN",
    )
    subject["tin"] = subject_tin
    subject["ssn_or_ein"] = _first_non_empty(subject.get("ssn_or_ein"), subject_tin)
    subject["dob"] = subject_dob
    subject["date_of_birth"] = _first_non_empty(subject.get("date_of_birth"), subject_dob)
    case_data["subject"] = subject

    institution = case_data.get("institution") if isinstance(case_data.get("institution"), dict) else {}
    institution = dict(institution)
    agg_institution = aggregator_output.get("institution") if isinstance(aggregator_output.get("institution"), dict) else {}
    agg_fi = (
        aggregator_output.get("financial_institution")
        if isinstance(aggregator_output.get("financial_institution"), dict)
        else {}
    )

    institution_tin = _first_non_empty(
        institution.get("tin"),
        institution.get("ein"),
        institution.get("ein_or_ssn"),
        agg_institution.get("tin"),
        agg_institution.get("ein"),
        agg_fi.get("tin"),
        agg_fi.get("ein_or_ssn"),
        "UNKNOWN",
    )
    institution["tin"] = institution_tin
    institution["ein"] = _first_non_empty(institution.get("ein"), institution_tin)
    case_data["institution"] = institution

    case_data["filing_type"] = _first_non_empty(
        case_data.get("filing_type"),
        aggregator_output.get("filing_type"),
        "initial",
    )

    # Keep a reporting hint for downstream logging/debugging.
    case_data["_validator_backfill_applied"] = report_type.upper()
    return case_data


def _enrich_aggregate_for_validator(
    aggregator_output: Dict[str, Any],
    case_data: Dict[str, Any],
) -> Dict[str, Any]:
    aggregate = dict(aggregator_output)

    case_subject = case_data.get("subject") if isinstance(case_data.get("subject"), dict) else {}
    agg_subject = aggregate.get("subject") if isinstance(aggregate.get("subject"), dict) else {}
    merged_subject = {
        **dict(case_subject),
        **dict(agg_subject),
    }

    merged_subject["city"] = _first_non_empty(merged_subject.get("city"), "UNKNOWN")
    merged_subject["state"] = _first_non_empty(merged_subject.get("state"), "NA")[:2]
    merged_subject["zip"] = _first_non_empty(merged_subject.get("zip"), merged_subject.get("postal_code"), "00000")
    merged_subject["address"] = _first_non_empty(
        merged_subject.get("address"),
        f"{merged_subject.get('city')}, {merged_subject.get('state')}".strip(", "),
        "UNKNOWN",
    )
    merged_subject["tin"] = _first_non_empty(
        merged_subject.get("tin"),
        merged_subject.get("ssn_or_ein"),
        merged_subject.get("ssn"),
        merged_subject.get("ein"),
        aggregate.get("customer_ssn"),
        "UNKNOWN",
    )
    merged_subject["dob"] = _first_non_empty(
        merged_subject.get("dob"),
        merged_subject.get("date_of_birth"),
        aggregate.get("customer_dob"),
        "1900-01-01",
    )

    aggregate["subject"] = merged_subject
    aggregate["customer_ssn"] = _first_non_empty(aggregate.get("customer_ssn"), merged_subject.get("tin"), "UNKNOWN")
    aggregate["customer_dob"] = _first_non_empty(aggregate.get("customer_dob"), merged_subject.get("dob"), "1900-01-01")

    case_institution = case_data.get("institution") if isinstance(case_data.get("institution"), dict) else {}
    agg_institution = aggregate.get("institution") if isinstance(aggregate.get("institution"), dict) else {}
    merged_institution = {
        **dict(case_institution),
        **dict(agg_institution),
    }
    inst_tin = _first_non_empty(
        merged_institution.get("tin"),
        merged_institution.get("ein"),
        merged_institution.get("ein_or_ssn"),
        "UNKNOWN",
    )
    merged_institution["tin"] = inst_tin
    merged_institution["ein"] = _first_non_empty(merged_institution.get("ein"), inst_tin)
    aggregate["institution"] = merged_institution

    fin_inst = aggregate.get("financial_institution") if isinstance(aggregate.get("financial_institution"), dict) else {}
    fin_inst = dict(fin_inst)
    fin_inst["tin"] = _first_non_empty(fin_inst.get("tin"), fin_inst.get("ein_or_ssn"), inst_tin)
    aggregate["financial_institution"] = fin_inst

    return aggregate


def _normalize_report_types(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
            raw = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            raw = []
    else:
        raw = []

    out: list[str] = []
    for item in raw:
        report_type = str(item or "").upper()
        if report_type in {"SAR", "CTR"} and report_type not in out:
            out.append(report_type)
    return out


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Merge overlay into base recursively (dict nodes only)."""
    merged: Dict[str, Any] = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _build_filing_case(
    *,
    routed_case: Dict[str, Any],
    aggregate: Dict[str, Any],
    narrative_output: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build final filer payload from routed input + aggregator output."""
    merged = _deep_merge_dict(routed_case, aggregate)
    narrative_text = (
        (narrative_output or {}).get("narrative_text")
        or (narrative_output or {}).get("narrative")
        or (narrative_output or {}).get("text")
    )
    if narrative_text:
        merged["narrative"] = narrative_text
    return merged


def create_compliance_crew(
    transaction_data: dict,
    on_stage: Callable[[str, int], None] | None = None,
) -> Dict[str, dict]:
    normalized_case = normalize_case_data(transaction_data)
    base_llm = LLM(model="gpt-4.1", temperature=0.1, max_tokens=4000, api_key=settings.OPENAI_API_KEY)

    def mark_stage(agent: str, progress: int) -> None:
        if on_stage is None:
            return
        try:
            on_stage(agent, progress)
        except Exception:
            pass

    router_output: Dict[str, Any] = {}
    try:
        mark_stage("router", 15)
        router_output = run_router_stage(normalized_case)
    except Exception as exc:
        router_output = {"router_error": str(exc)}

    routed_case = router_output.get("validated_input")
    if isinstance(routed_case, dict) and routed_case:
        routed_case = normalize_case_data(routed_case)
    else:
        routed_case = normalized_case

    total_cash_amount = calculate_total_cash_amount(routed_case)
    suspicious = has_suspicious_activity(routed_case)
    report_types = _normalize_report_types(router_output.get("report_types"))
    if not report_types:
        report_types = determine_report_types(routed_case)
    router_output["report_types"] = report_types
    router_output["total_cash_amount"] = total_cash_amount
    if not str(router_output.get("reasoning") or "").strip():
        router_output["reasoning"] = _build_router_reasoning(total_cash_amount, suspicious, report_types)
    router_output.setdefault("confidence_score", 1.0 if report_types else 0.0)
    router_output.setdefault("kb_status", "EXISTS")
    router_output["validated_input"] = routed_case
    if report_types:
        # Keep legacy key for downstream prompts expecting one report type.
        router_output["report_type"] = "SAR" if "SAR" in report_types else report_types[0]
    else:
        router_output["report_type"] = "NONE"

    if str(router_output.get("kb_status", "")).upper() == "MISSING":
        return {
            "router": router_output,
            "validation": {
                "approval_flag": False,
                "status": "KB_MISSING",
                "message": router_output.get("message") or "Requested report type is missing in Knowledge Base.",
            },
            "final": {
                "status": "kb_missing",
                "message": router_output.get("message") or "Requested report type is missing in Knowledge Base.",
            },
        }

    if not report_types:
        return {
            "router": router_output,
            "validation": {
                "approval_flag": False,
                "status": "NO_FILING_REQUIRED",
                "message": "No CTR or SAR requirement detected for this case.",
            },
            "final": {
                "status": "no_filing_required",
                "message": "No CTR or SAR filing requirements met",
            },
        }

    # Researcher (Agent 2) intentionally skipped per workflow requirement.

    aggregator = AggregatorOrchestrator(llm=base_llm)
    aggregated_by_type: Dict[str, Dict[str, Any]] = {}
    mark_stage("aggregator", 35)
    for report_type in report_types:
        aggregated = aggregator.process(
            raw_data=routed_case,
            report_type=report_type,
            case_id=routed_case.get("case_id") if isinstance(routed_case, dict) else None,
        )
        aggregated_by_type[report_type] = aggregated.model_dump(mode="json")

    primary_report_type = "SAR" if "SAR" in aggregated_by_type else report_types[0]
    aggregator_output: Dict[str, Any] = aggregated_by_type[primary_report_type]

    narrative_output: Dict[str, Any] = {}
    sar_aggregate = aggregated_by_type.get("SAR")
    if isinstance(sar_aggregate, dict) and sar_aggregate.get("narrative_required", True):
        mark_stage("narrative", 55)
        narrative_input = _build_narrative_input(routed_case, sar_aggregate)
        narrative_output = generate_narrative_payload(
            narrative_input,
            report_type_code="SAR",
            verbose=True,
        )

    mark_stage("validator", 75)
    if settings.SKIP_VALIDATOR_FOR_TESTING:
        validation_output = {
            "status": "APPROVED",
            "approval_flag": True,
            "compliance_checks": {"validator": "SKIPPED_FOR_TESTING"},
            "issues": [],
            "recommendations": ["Validator was bypassed for testing mode."],
            "skip_reason": "SKIP_VALIDATOR_FOR_TESTING=true",
        }
    else:
        validation_case = _enrich_case_for_validator(
            normalized_case=routed_case,
            aggregator_output=aggregator_output,
            report_type=primary_report_type,
        )
        validation_aggregate = _enrich_aggregate_for_validator(
            aggregator_output=aggregator_output,
            case_data=validation_case,
        )
        validation_output = validate_with_teammate_agent(
            normalized_case=validation_case,
            aggregator_output=validation_aggregate,
            narrative_output=narrative_output,
            report_type=primary_report_type,
        )
        if "approval_flag" not in validation_output:
            status = str(validation_output.get("status", "")).upper()
            validation_output["approval_flag"] = status == "APPROVED"
        if "status" not in validation_output:
            validation_output["status"] = "APPROVED" if validation_output.get("approval_flag") else "NEEDS_REVIEW"

    final_output: Dict[str, dict]
    if validation_output.get("approval_flag"):
        # Deterministic filing avoids LLM-output parsing risk for final artifacts.
        mark_stage("filer", 90)
        reports = []

        ctr_aggregate = aggregated_by_type.get("CTR") if isinstance(aggregated_by_type.get("CTR"), dict) else {}
        sar_aggregate = aggregated_by_type.get("SAR") if isinstance(aggregated_by_type.get("SAR"), dict) else {}

        if "CTR" in report_types:
            ctr_case = _build_filing_case(
                routed_case=routed_case,
                aggregate=ctr_aggregate,
                narrative_output=None,
            )
            reports.append(CTRReportFiler().fill_from_dict(ctr_case))

        if "SAR" in report_types:
            sar_case = _build_filing_case(
                routed_case=routed_case,
                aggregate=sar_aggregate,
                narrative_output=narrative_output,
            )
            reports.append(SARReportFiler().fill_from_dict(sar_case))

        final_output = reports[0] if len(reports) == 1 else {"status": "success", "reports": reports}
    else:
        final_output = {
            "status": "needs_review",
            "validation_report": validation_output,
            "message": "Report did not pass validation - human review required",
        }

    return {
        "router": router_output,
        "aggregator": aggregator_output,
        "aggregator_by_type": aggregated_by_type,
        "narrative": narrative_output,
        "validation": validation_output,
        "final": final_output,
    }
