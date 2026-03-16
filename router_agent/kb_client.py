"""
Knowledge Base client for the router: check report type existence and fetch schema/required fields.
- When Supabase REST is enabled (SUPABASE_URL + SUPABASE_ANON_KEY): uses your tables
  report_types (report_type_code) and required_fields (input_key, is_required, ask_user_prompt).
- Otherwise: uses backend KBManager (Postgres report_schemas or legacy REST).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from router_agent import supabase_rest
from router_agent.schema_validator import get_required_field_paths as schema_required_paths

# Fallback when Supabase REST not used
from backend.knowledge_base.kb_manager import KBManager

_kb: Optional[KBManager] = None


def _get_kb() -> KBManager:
    global _kb
    if _kb is None:
        _kb = KBManager()
    return _kb


def _use_supabase_rest() -> bool:
    return supabase_rest._rest_enabled()


def report_type_exists(report_type: str) -> bool:
    """
    Check if the given report type exists in the Knowledge Base.
    Uses Supabase report_types.report_type_code when REST is enabled, else KBManager.
    """
    if not (report_type and str(report_type).strip()):
        return False
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        row = supabase_rest.fetch_report_type_row(rt)
        return row is not None and (row.get("is_active") is not False)
    try:
        _get_kb().get_schema(rt)
        return True
    except ValueError as e:
        logger.debug("Report type %s not in KB: %s", rt, e)
        return False
    except Exception as e:
        logger.warning("KB check failed for %s: %s", rt, e)
        return False


def get_report_schema(report_type: str) -> Dict[str, Any]:
    """
    Fetch the JSON schema for the report type.
    Uses Supabase report_types.json_schema (report_type_code) when REST enabled, else KBManager.
    Raises ValueError if report type is not found.
    """
    if not (report_type and str(report_type).strip()):
        raise ValueError("report_type is required")
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        schema = supabase_rest.get_report_type_schema(rt)
        if schema is None:
            raise ValueError(f"Schema not found in Supabase report_types for report_type_code={rt}")
        return schema
    return _get_kb().get_schema(rt)


def get_required_field_paths(report_type: str) -> List[str]:
    """
    Return required field paths for this report type for validation.
    - When Supabase REST is enabled: uses only required_fields table where is_required=TRUE
      (no schema fallback, so the form's required_fields is the single source of truth).
    - Otherwise: uses report schema (from KBManager) and schema_validator to derive paths.
    """
    if not (report_type and str(report_type).strip()):
        return []
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        return supabase_rest.get_required_input_keys(rt)
    schema = get_report_schema(rt)
    return schema_required_paths(schema, rt)


def get_required_fields_with_prompts(report_type: str) -> List[Dict[str, Any]]:
    """
    Return required fields with ask_user_prompt for multi-turn collection (is_required=TRUE only).
    When Supabase is enabled: from required_fields table. Otherwise: empty list.
    Each item: input_key, ask_user_prompt, field_label.
    """
    if not (report_type and str(report_type).strip()):
        return []
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        return supabase_rest.get_required_fields_with_prompts(rt)
    return []
