"""
Supabase REST client for router_agent using your table structure:
- report_types (report_type_code, json_schema, narrative_instructions, ...)
- required_fields (report_type_code, input_key, field_label, is_required, ask_user_prompt, ...)
Uses SUPABASE_URL and SUPABASE_ANON_KEY from backend.config.settings.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from backend.config.settings import settings


def _rest_enabled() -> bool:
    url = (settings.get_supabase_rest_url() or "").strip()
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return bool(url.startswith(("http://", "https://")) and key)


def _headers() -> Dict[str, str]:
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_report_type_row(report_type_code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch one row from report_types where report_type_code = report_type_code.
    Column names: report_type_code, display_name, json_schema, narrative_instructions, etc.
    """
    if not _rest_enabled():
        return None
    code = str(report_type_code).strip().upper()
    url = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{url}/rest/v1/report_types"
    params = {
        "select": "id,report_type_code,display_name,narrative_required,narrative_instructions,json_schema,validation_rules,pdf_template_path,pdf_field_mapping,is_active",
        "report_type_code": f"eq.{code}",
        "limit": "1",
    }
    try:
        r = requests.get(full_url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if isinstance(rows, list) and rows:
            return rows[0]
        return None
    except Exception as e:
        logger.warning("fetch_report_type_row %s failed: %s", code, e)
        return None


def fetch_required_fields(report_type_code: str, required_only: bool = True) -> List[Dict[str, Any]]:
    """
    Fetch rows from required_fields for the given report_type_code.
    Returns list of dicts with input_key, field_label, ask_user_prompt, is_required, etc.
    """
    if not _rest_enabled():
        return []
    code = str(report_type_code).strip().upper()
    url = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{url}/rest/v1/required_fields"
    params = {
        "select": "id,report_type_code,field_number,field_label,part,field_type,is_required,input_key,conditional_note,ask_user_prompt",
        "report_type_code": f"eq.{code}",
        "order": "id.asc",
    }
    if required_only:
        params["is_required"] = "eq.true"
    try:
        r = requests.get(full_url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logger.warning("fetch_required_fields %s failed: %s", code, e)
        return []


def get_required_input_keys(report_type_code: str) -> List[str]:
    """
    Return list of input_key values from required_fields for this report type (is_required=true).
    Used as required field paths for validation.
    """
    rows = fetch_required_fields(report_type_code, required_only=True)
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("input_key"):
            out.append(str(r["input_key"]).strip())
    return sorted(set(out))


def get_required_fields_with_prompts(report_type_code: str) -> List[Dict[str, Any]]:
    """
    Return required fields (is_required=true) with ask_user_prompt and field_label
    for multi-turn collection of missing values. Each item: input_key, ask_user_prompt, field_label.
    """
    rows = fetch_required_fields(report_type_code, required_only=True)
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("input_key"):
            continue
        out.append({
            "input_key": str(r["input_key"]).strip(),
            "ask_user_prompt": str(r.get("ask_user_prompt") or "").strip() or r.get("field_label") or r["input_key"],
            "field_label": str(r.get("field_label") or r["input_key"]).strip(),
        })
    return out


def get_report_type_schema(report_type_code: str) -> Optional[Dict[str, Any]]:
    """
    Return json_schema from report_types for this report_type_code, or None if not found.
    """
    row = fetch_report_type_row(report_type_code)
    if not row:
        return None
    raw = row.get("json_schema")
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None
