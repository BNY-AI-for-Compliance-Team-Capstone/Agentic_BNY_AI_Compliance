from __future__ import annotations

import ast
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.agent_timeline import render_agent_timeline
from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.report_preview import render_pdf_preview
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.components.status_badge import status_badge
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.formatting import format_currency
from streamlit_app.utils.session_state import add_tracked_job, init_session_state

TEAMMATE_ROUTER_ROOT = PROJECT_ROOT / "Agent 1  - Router Agent"
if TEAMMATE_ROUTER_ROOT.exists() and str(TEAMMATE_ROUTER_ROOT) not in sys.path:
    sys.path.insert(0, str(TEAMMATE_ROUTER_ROOT))

try:
    from router_agent.run import run_router
    _router_import_error: Exception | None = None
except Exception as exc:
    run_router = None
    _router_import_error = exc


AGENT_ORDER = ["router", "aggregator", "narrative", "validator", "filer"]
STAGE_LABELS = {
    "router": "Classification",
    "aggregator": "Data Preparation",
    "narrative": "Narrative Drafting",
    "validator": "Quality Checks",
    "filer": "Report Filing",
}


def _result_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload = details.get("result")
    return payload if isinstance(payload, dict) else {}


def _report_types(details: dict[str, Any]) -> list[str]:
    report_types = details.get("report_types")
    if isinstance(report_types, list):
        return [str(item).upper() for item in report_types]
    router_types = (_result_payload(details).get("router") or {}).get("report_types")
    if isinstance(router_types, list):
        return [str(item).upper() for item in router_types]
    return []


def _narrative_required(result: dict[str, Any], report_types: list[str]) -> bool:
    by_type = result.get("aggregator_by_type")
    if isinstance(by_type, dict):
        sar = by_type.get("SAR")
        if isinstance(sar, dict):
            return bool(sar.get("narrative_required", True))
    return "SAR" in report_types


def _timeline(details: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(details.get("status", "pending")).lower()
    current = str(details.get("current_agent") or "").lower()
    result = _result_payload(details)
    report_types = _report_types(details)
    narrative_required = _narrative_required(result, report_types)
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
    needs_review = str(final_payload.get("status", "")).lower() == "needs_review"
    current_index = AGENT_ORDER.index(current) if current in AGENT_ORDER else -1

    description = {
        "router": (result.get("router") or {}).get("reasoning", "Classifying filing type"),
        "aggregator": "Mapping case fields and risk flags",
        "narrative": "Generating SAR narrative section",
        "validator": ((result.get("validation") or {}).get("status") or "Running validation checks"),
        "filer": (final_payload.get("status") or "Generating PDF output"),
    }

    out: list[dict[str, Any]] = []
    for idx, agent in enumerate(AGENT_ORDER):
        if agent == "narrative" and not narrative_required:
            state = "skipped"
            desc = "Narrative not required for this report type"
        elif status == "completed":
            if agent == "filer" and needs_review:
                state = "skipped"
                desc = "Skipped because validation requires human review"
            else:
                state = "completed"
                desc = description.get(agent, "")
        elif status in {"failed", "error"}:
            if current == agent:
                state = "error"
            elif current_index >= 0 and idx < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = description.get(agent, "")
        else:
            if current == agent:
                state = "active"
            elif current_index >= 0 and idx < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = description.get(agent, "")
        out.append({"name": STAGE_LABELS.get(agent, agent.title()), "status": state, "description": desc})
    return out


def _rows_from_dict(data: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, default=str)
        else:
            value_str = str(value)
        rows.append({"Field": str(key), "Value": value_str})
    return rows


def _submit_payload(api_client: APIClient, payload: dict[str, Any]) -> None:
    try:
        with st.spinner("Submitting case for processing..."):
            result = api_client.submit_case(payload)
        job_id = result.get("job_id")
        if not job_id:
            st.error("Submission succeeded but job_id was not returned.")
            return
        add_tracked_job(job_id)
        st.session_state["selected_job_id"] = job_id
        st.success(f"Case submitted successfully. Job ID: {job_id}")
    except APIClientError as exc:
        st.error(str(exc))


def _set_nested(payload: dict[str, Any], path: str, value: str) -> None:
    if not path.strip():
        return
    parts = path.split(".")
    current: dict[str, Any] = payload
    for key in parts[:-1]:
        existing = current.get(key)
        if not isinstance(existing, dict):
            current[key] = {}
        current = current[key]  # type: ignore[assignment]
    current[parts[-1]] = value


def _parse_json_or_python_dict(text: str) -> Any:
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return ast.literal_eval(raw)


def _router_result_to_dict(result_obj: Any) -> dict[str, Any]:
    if hasattr(result_obj, "to_dict"):
        data = result_obj.to_dict()
        return data if isinstance(data, dict) else {}
    if isinstance(result_obj, dict):
        return result_obj
    return {}


def _run_router(payload: Any) -> dict[str, Any]:
    if run_router is None:
        detail = f" (import error: {_router_import_error})" if _router_import_error else ""
        raise RuntimeError(
            "Teammate Router UI module is not available. "
            f"Check Agent 1 folder and dependencies{detail}"
        )
    return _router_result_to_dict(run_router(payload))


def _router_validated_payload(router_result: dict[str, Any], fallback_payload: dict[str, Any]) -> dict[str, Any]:
    validated = router_result.get("validated_input") if isinstance(router_result, dict) else None
    if isinstance(validated, dict) and validated:
        return validated
    return fallback_payload


def _submit_router_payload(payload: dict[str, Any], backend_url: str, default_api_client: APIClient) -> None:
    submit_client = default_api_client
    url = (backend_url or "").strip()
    if url and url.rstrip("/") != default_api_client.base_url.rstrip("/"):
        submit_client = APIClient(base_url=url, timeout=default_api_client.timeout)
    _submit_payload(submit_client, payload)


def _render_router_result(router_result: dict[str, Any], payload: dict[str, Any]) -> None:
    effective_payload = _router_validated_payload(router_result, payload)
    st.subheader("Router result")
    st.table(
        [
            {"Field": "Report type", "Value": router_result.get("report_type")},
            {"Field": "Report types", "Value": router_result.get("report_types")},
            {"Field": "Knowledge base", "Value": router_result.get("kb_status")},
            {"Field": "Confidence", "Value": router_result.get("confidence_score")},
            {"Field": "Reasoning", "Value": router_result.get("reasoning") or "(none)"},
        ]
    )

    st.markdown("**Message**")
    missing_fields = router_result.get("missing_fields") if isinstance(router_result.get("missing_fields"), list) else []
    missing_prompts = (
        router_result.get("missing_field_prompts")
        if isinstance(router_result.get("missing_field_prompts"), list)
        else []
    )

    if missing_fields:
        st.info(str(router_result.get("message") or "Missing required fields."))
        with st.expander(f"Show missing required fields ({len(missing_fields)})"):
            st.code(", ".join(str(item) for item in missing_fields), language=None)

        if missing_prompts:
            chat_messages = st.session_state.setdefault("router_chat_messages", [])
            current_prompt = missing_prompts[0] if missing_prompts else {}
            current_question = (
                current_prompt.get("ask_user_prompt")
                or current_prompt.get("field_label")
                or current_prompt.get("input_key", "")
            )
            current_input_key = current_prompt.get("input_key", "")

            st.markdown("---")
            st.markdown("#### Collect missing information (chat)")
            st.caption("Reply one field at a time. Router will re-validate automatically.")

            for msg in chat_messages:
                with st.chat_message(msg.get("role", "assistant")):
                    st.write(msg.get("content", ""))

            with st.chat_message("assistant"):
                st.write(current_question)

            reply = st.chat_input("Type your answer and press Enter", key="router_chat_input")
            if reply and str(current_input_key).strip():
                merged = copy.deepcopy(effective_payload)
                _set_nested(merged, str(current_input_key), reply.strip())
                new_messages = chat_messages + [
                    {"role": "assistant", "content": current_question, "input_key": current_input_key},
                    {"role": "user", "content": reply.strip()},
                ]
                try:
                    with st.spinner("Checking..."):
                        new_result = _run_router(merged)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state["router_result"] = new_result
                    st.session_state["router_pending_payload"] = _router_validated_payload(new_result, merged)
                    st.session_state["router_chat_messages"] = new_messages
                    st.rerun()
        return

    if st.session_state.get("router_chat_messages"):
        st.markdown("---")
        st.markdown("#### Dialogue")
        for msg in st.session_state.get("router_chat_messages", []):
            with st.chat_message(msg.get("role", "assistant")):
                st.write(msg.get("content", ""))
        with st.chat_message("assistant"):
            st.write("All required fields are filled. You can submit to pipeline.")

    st.success(str(router_result.get("message") or "Ready for pipeline."))
    st.markdown("---")
    st.markdown("#### Complete case JSON (for pipeline)")
    st.caption("This payload will be passed to Agent 3 via the backend pipeline.")

    json_str = json.dumps(effective_payload, indent=2)
    st.code(json_str, language="json")

    case_id = effective_payload.get("case_id") if isinstance(effective_payload, dict) else None
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(case_id or "case"))[:64]
    st.download_button(
        label="Download complete JSON",
        data=json_str,
        file_name=f"{safe_id}_complete.json",
        mime="application/json",
        key="router_download_complete_json",
    )


def _render_router_intake_workspace(api_client: APIClient) -> None:
    st.markdown("### Router Agent - Case Intake")
    st.caption("Classify report type, validate required fields, then submit to the full pipeline.")

    default_backend_url = st.session_state.get("router_backend_url", api_client.base_url)
    backend_url = st.text_input("API base URL", value=default_backend_url, key="router_backend_url")

    tab_text, tab_json = st.tabs(["Text input", "JSON input"])

    with tab_text:
        st.markdown("#### Free-text case description")
        subject = st.text_input("Subject name", value="Unknown Subject", key="router_text_subject")
        text_input = st.text_area(
            "Case description",
            height=200,
            placeholder="E.g. File SAR for suspicious wire transfers with structuring behavior.",
            key="router_text_input",
        )
        if st.button("Classify & validate", key="router_btn_text"):
            if not text_input.strip():
                st.warning("Enter case text.")
            else:
                try:
                    with st.spinner("Running router..."):
                        result = _run_router(text_input.strip())
                except Exception as exc:
                    st.error(str(exc))
                else:
                    seed_payload = {
                        "subject": {"name": subject},
                        "case_description": text_input,
                    }
                    st.session_state["router_result"] = result
                    st.session_state["router_pending_payload"] = _router_validated_payload(result, seed_payload)
                    st.session_state["router_chat_messages"] = []

    with tab_json:
        st.markdown("#### Paste case JSON")
        st.caption("Accepts strict JSON or Python dict format (single quotes, True/False/None).")
        json_raw = st.text_area(
            "JSON",
            height=200,
            placeholder='{"report_type": "SAR", "subject": {"name": "..."}}',
            key="router_json_input",
        )
        if st.button("Classify & validate", key="router_btn_json"):
            if not json_raw.strip():
                st.warning("Enter or paste JSON.")
            else:
                try:
                    payload = _parse_json_or_python_dict(json_raw)
                except (json.JSONDecodeError, ValueError, SyntaxError) as exc:
                    st.error(f"Invalid input: {exc}")
                else:
                    if isinstance(payload, list) and payload:
                        payload = payload[0]
                    if not isinstance(payload, dict):
                        st.error("JSON must be an object or an array of objects.")
                    else:
                        try:
                            with st.spinner("Running router..."):
                                result = _run_router(payload)
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["router_result"] = result
                            st.session_state["router_pending_payload"] = _router_validated_payload(result, payload)
                            st.session_state["router_chat_messages"] = []

    st.markdown("---")
    st.markdown("### Next: submit to full pipeline")

    router_result = st.session_state.get("router_result")
    pending_payload = st.session_state.get("router_pending_payload")
    if isinstance(router_result, dict) and isinstance(pending_payload, dict):
        pipeline_payload = _router_validated_payload(router_result, pending_payload)
        st.session_state["router_pending_payload"] = pipeline_payload
        _render_router_result(router_result, pipeline_payload)
        if st.button("Submit to full pipeline", type="primary", key="router_submit_pipeline"):
            _submit_router_payload(pipeline_payload, backend_url, api_client)
            st.session_state.pop("router_result", None)
            st.session_state.pop("router_pending_payload", None)
    else:
        st.caption("Use Text input or JSON input above and click Classify & validate, then submit here.")


def _render_agent_outputs(details: dict[str, Any], api_client: APIClient) -> None:
    result = _result_payload(details)
    router = result.get("router") if isinstance(result.get("router"), dict) else {}
    by_type = result.get("aggregator_by_type") if isinstance(result.get("aggregator_by_type"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    narrative = result.get("narrative") if isinstance(result.get("narrative"), dict) else {}
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}

    report_types = _report_types(details)
    primary = "SAR" if "SAR" in report_types else ("CTR" if "CTR" in report_types else "")
    aggregate = by_type.get(primary) if isinstance(by_type.get(primary), dict) else {}
    if not aggregate and isinstance(result.get("aggregator"), dict):
        aggregate = result.get("aggregator")

    st.markdown("#### Classification")
    missing_fields = router.get("missing_fields") if isinstance(router.get("missing_fields"), list) else []
    if missing_fields:
        st.warning("Some required router fields are missing. Add them and resubmit for best results.")

    st.table(
        _rows_from_dict(
            {
                "Report Types": router.get("report_types", []),
                "Reasoning": router.get("reasoning"),
                "Message": router.get("message"),
                "Confidence Score": router.get("confidence_score"),
                "Knowledge Base Status": router.get("kb_status"),
                "Missing Fields": missing_fields,
            }
        )
    )

    st.markdown("#### Data Preparation")
    st.table(
        _rows_from_dict(
            {
                "Report Type": aggregate.get("report_type"),
                "Case ID": aggregate.get("case_id"),
                "Total Amount Involved": aggregate.get("total_amount_involved"),
                "Risk Score": aggregate.get("risk_score"),
                "Missing Required Fields": aggregate.get("missing_required_fields", []),
                "Narrative Required": aggregate.get("narrative_required"),
            }
        )
    )

    st.markdown("#### Narrative Drafting")
    if narrative.get("narrative_text"):
        st.success("Narrative generated.")
        st.write(narrative.get("narrative_text"))
    else:
        st.info("Narrative step skipped (not required for this report type).")

    st.markdown("#### Quality Checks")
    if validation:
        status_badge(str(validation.get("status", "unknown")).lower().replace(" ", "_"))
        st.write("")
        st.table(
            _rows_from_dict(
                {
                    "Approval Flag": validation.get("approval_flag"),
                    "Status": validation.get("status"),
                    "Completeness Score": validation.get("completeness_score"),
                    "Compliance Checks": validation.get("compliance_checks", {}),
                    "Issues": validation.get("issues", []),
                    "Recommendations": validation.get("recommendations", []),
                }
            )
        )
    else:
        st.info("Validation output is not available yet.")

    st.markdown("#### Report Filing")
    filed_reports: list[dict[str, Any]] = []
    if isinstance(final_payload.get("reports"), list):
        for item in final_payload["reports"]:
            if isinstance(item, dict):
                filed_reports.append(item)
    elif final_payload.get("pdf_path"):
        filed_reports.append(final_payload)

    if filed_reports:
        st.success("Filing completed.")
        rows = []
        for item in filed_reports:
            rows.append(
                {
                    "Report Type": item.get("report_type", ""),
                    "Fields Filled": item.get("fields_filled", ""),
                    "Attempted Fields": item.get("attempted_fields", ""),
                    "Template Fields": item.get("template_field_count", ""),
                    "Template": item.get("template_variant", item.get("template_path", "")),
                    "Generated At": item.get("generated_at", ""),
                }
            )
        st.table(rows)

        for report in filed_reports:
            report_type = str(report.get("report_type") or "").upper()
            if not report_type:
                continue
            button_key = f"submit_page_download_{details.get('job_id')}_{report_type}"
            if st.button(f"Download {report_type} PDF", key=button_key):
                try:
                    pdf_bytes = api_client.download_report(details["job_id"], report_type=report_type)
                    render_pdf_preview(pdf_bytes, f"{report_type}_{details.get('job_id')}.pdf")
                except APIClientError as exc:
                    st.error(str(exc))
    elif str(final_payload.get("status", "")).lower() == "needs_review":
        st.error("Needs human review. Filing skipped.")
        st.markdown("##### Case Summary for Reviewer")
        st.write(f"Case ID: {aggregate.get('case_id', details.get('job_id'))}")
        st.write(f"Report Types: {', '.join(report_types) if report_types else '-'}")
        st.write(f"Total Amount: {format_currency(aggregate.get('total_amount_involved', 0))}")
        issues = validation.get("issues", [])
        if isinstance(issues, list) and issues:
            st.write("Reasons:")
            for issue in issues:
                st.write(f"- {issue}")
    else:
        st.info("Filer output is not available yet.")


def _monitor_job(api_client: APIClient) -> None:
    selected_job = st.session_state.get("selected_job_id")
    if not selected_job:
        return

    st.markdown("---")
    st.markdown(f"### Live Workflow Monitor - {selected_job}")
    auto_refresh = st.toggle("Auto-refresh monitor", value=True, key="submit_monitor_auto_refresh")

    try:
        details = api_client.get_job_status(selected_job)
    except APIClientError as exc:
        st.error(str(exc))
        return

    top1, top2, top3 = st.columns(3)
    with top1:
        st.metric("Job Status", str(details.get("status", "unknown")).upper())
    with top2:
        current_stage_key = str(details.get("current_agent") or "").lower()
        st.metric("Current Stage", STAGE_LABELS.get(current_stage_key, "-"))
    with top3:
        st.metric("Progress", f"{int(details.get('progress') or 0)}%")
    st.progress(int(details.get("progress") or 0))

    tab_timeline, tab_outputs, tab_table = st.tabs(["Workflow Progress", "Case Summary", "Processing Table"])
    with tab_timeline:
        render_agent_timeline(_timeline(details))
    with tab_outputs:
        _render_agent_outputs(details, api_client)
    with tab_table:
        summary_rows = [
            {"Field": "Job ID", "Value": str(details.get("job_id", ""))},
            {"Field": "Status", "Value": str(details.get("status", ""))},
            {"Field": "Current Stage", "Value": STAGE_LABELS.get(str(details.get("current_agent") or "").lower(), "-")},
            {"Field": "Progress", "Value": f"{int(details.get('progress') or 0)}%"},
            {"Field": "Report Types", "Value": ", ".join(_report_types(details))},
        ]
        result = _result_payload(details)
        final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
        if final_payload:
            summary_rows.append({"Field": "Final Status", "Value": str(final_payload.get("status", ""))})
            summary_rows.append({"Field": "Final Message", "Value": str(final_payload.get("message", ""))})
        st.table(summary_rows)

    if auto_refresh and str(details.get("status", "")).lower() in {"submitted", "processing"}:
        time.sleep(max(int(st.session_state.get("notifications_refresh_seconds", 5)), 2))
        st.rerun()


st.set_page_config(
    page_title="Submit Case",
    page_icon=settings.page_icon,
    layout=settings.layout,
    initial_sidebar_state="expanded",
)
init_session_state()
load_styles()

if st.session_state.get("api_client") is None:
    st.session_state["api_client"] = APIClient(
        base_url=st.session_state.get("settings_api_url", settings.api_base_url),
        timeout=int(st.session_state.get("settings_timeout", settings.request_timeout_seconds)),
    )
api_client: APIClient = st.session_state["api_client"]

render_sidebar(api_client)
render_header("Submit New Case", "Submit text or structured data for end-to-end compliance processing")

_render_router_intake_workspace(api_client)

_monitor_job(api_client)
