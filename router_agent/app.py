"""
Standalone Streamlit frontend for the Router Agent.
Run from repo root: streamlit run router_agent/app.py --server.port 8502

Provides: user input (text / JSON) -> router (classify + KB + validation) -> show result -> submit to backend.
All logic and UI live in router_agent; no changes to backend or main Streamlit app.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

# Ensure project root is on path when run as: streamlit run router_agent/app.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests
import streamlit as st

from router_agent import run_router, RouterResult
from router_agent.config import API_BASE_URL


def _submit_to_backend(payload: dict, base_url: str | None = None) -> tuple[bool, str]:
    """POST payload to existing backend /api/v1/reports/submit. Returns (success, message)."""
    base = (base_url or API_BASE_URL).strip()
    url = f"{base.rstrip('/')}/api/v1/reports/submit"
    try:
        r = requests.post(
            url,
            json={"transaction_data": payload},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if r.status_code >= 400:
            return False, r.text or f"HTTP {r.status_code}"
        data = r.json() if r.content else {}
        job_id = data.get("job_id") or ""
        return True, f"Submitted. Job ID: {job_id}"
    except requests.ConnectionError:
        return False, f"Cannot connect to backend at {url}. Is the API running?"
    except Exception as e:
        return False, str(e)


def _render_router_result(result: RouterResult) -> None:
    st.subheader("Router result")
    st.table(
        [
            {"Field": "Report type", "Value": result.report_type},
            {"Field": "Report types", "Value": result.report_types},
            {"Field": "Knowledge base", "Value": result.kb_status},
            {"Field": "Confidence", "Value": result.confidence_score},
            {"Field": "Reasoning", "Value": result.reasoning or "(none)"},
        ]
    )
    st.markdown("**Message**")
    st.info(result.message)
    if result.missing_fields:
        st.warning(f"Missing required fields ({len(result.missing_fields)}): {', '.join(result.missing_fields)}")
    else:
        st.success("All required fields present. You can submit to the full pipeline.")


st.set_page_config(page_title="Router Agent – Submit Case", page_icon="📋", layout="centered")
st.title("Router Agent – Case Intake")
st.caption("Classify report type, validate required fields, then submit to the full pipeline.")

# Backend URL (editable in sidebar)
with st.sidebar:
    st.subheader("Backend")
    backend_url = st.text_input("API base URL", value=API_BASE_URL, key="backend_url")

t_text, t_json = st.tabs(["Text input", "JSON input"])

with t_text:
    st.markdown("#### Free-text case description")
    subject = st.text_input("Subject name", value="Unknown Subject", key="text_subject")
    text = st.text_area(
        "Case description",
        height=200,
        placeholder="E.g. I need to file a SAR for a suspicious wire transfer to a sanctioned country.",
        key="text_input",
    )
    if st.button("Classify & validate", key="btn_text"):
        if not text.strip():
            st.warning("Enter case text.")
        else:
            with st.spinner("Running router..."):
                result = run_router(text.strip())
            st.session_state["router_result"] = result
            st.session_state["pending_payload"] = {"subject": {"name": subject}, "case_description": text}

def _parse_json_or_python_dict(text: str):
    """Parse strict JSON first; if that fails, try Python dict literal (single quotes, True/False/None)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            raise json.JSONDecodeError("Use double-quoted JSON or a valid Python dict (single quotes, True/False/None)", text, 0)


with t_json:
    st.markdown("#### Paste or upload case JSON")
    st.caption("Accepts strict JSON (double quotes) or Python-style dict (single quotes, True/False/None).")
    json_raw = st.text_area("JSON", height=200, placeholder='{"report_type": "SAR", "subject": {"name": "..."}, ...}', key="json_input")
    if st.button("Classify & validate", key="btn_json"):
        if not json_raw.strip():
            st.warning("Enter or paste JSON.")
        else:
            try:
                payload = _parse_json_or_python_dict(json_raw)
                if isinstance(payload, list) and payload:
                    payload = payload[0]
                if not isinstance(payload, dict):
                    st.error("JSON must be an object or array of objects.")
                else:
                    with st.spinner("Running router..."):
                        result = run_router(payload)
                    st.session_state["router_result"] = result
                    st.session_state["pending_payload"] = payload
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
            except (ValueError, SyntaxError) as e:
                st.error(f"Invalid input: {e}")

st.markdown("---")
st.markdown("### Next: submit to full pipeline")

if st.session_state.get("router_result") and st.session_state.get("pending_payload"):
    _render_router_result(st.session_state["router_result"])
    payload = st.session_state["pending_payload"]
    base = st.session_state.get("backend_url", API_BASE_URL) or API_BASE_URL
    if st.button("Submit to full pipeline", type="primary", key="submit_full"):
        ok, msg = _submit_to_backend(payload, base_url=base)
        if ok:
            st.success(msg)
            st.session_state.pop("router_result", None)
            st.session_state.pop("pending_payload", None)
        else:
            st.error(msg)
else:
    st.caption("Use **Text input** or **JSON input** above and click **Classify & validate**, then submit here.")
