#!/usr/bin/env python3
"""End-to-end API check for SAR-only and CTR-only workflows."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Tuple

import requests


BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8001/api/v1").rstrip("/")
POLL_INTERVAL_SECONDS = 4
TIMEOUT_SECONDS = 240


def _request_json(method: str, url: str, **kwargs) -> Dict[str, Any]:
    response = requests.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        body = response.text.strip()
        raise RuntimeError(f"{method} {url} failed [{response.status_code}]: {body}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {url} returned non-object JSON: {type(payload)}")
    return payload


def submit_case(case_data: Dict[str, Any]) -> str:
    payload = {"transaction_data": case_data}
    response = _request_json("POST", f"{BASE_URL}/reports/submit", json=payload)
    job_id = response.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise RuntimeError(f"submit response missing job_id: {response}")
    return job_id


def wait_for_job(job_id: str) -> Dict[str, Any]:
    deadline = time.time() + TIMEOUT_SECONDS
    last_status = None
    while time.time() < deadline:
        status_payload = _request_json("GET", f"{BASE_URL}/reports/{job_id}/status")
        status = status_payload.get("status")
        if status != last_status:
            print(f"[{job_id}] status={status} progress={status_payload.get('progress')}")
            last_status = status
        if status in {"completed", "failed"}:
            return status_payload
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"job {job_id} did not finish in {TIMEOUT_SECONDS} seconds")


def build_test_cases() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    sar_case = {
        "case_id": "CASE-SAR-E2E-001",
        "subject": {
            "subject_id": "SUBJ-SAR-001",
            "name": "Structuring Test Subject",
            "type": "Individual",
            "country": "US",
        },
        "institution": {
            "name": "Example Community Bank",
            "branch_city": "Miami",
            "branch_state": "FL",
            "contact_officer": "Case Analyst",
            "contact_phone": "+1-305-555-1000",
            "primary_federal_regulator": "FDIC",
        },
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": 8000.0, "no_amount": False},
            "27_DateOrDateRange": {"from": "03/01/2024", "to": "03/03/2024"},
            "29_Structuring": ["Pattern of sub-threshold cash transactions"],
            "33_MoneyLaundering": ["Rapid funds movement"],
        },
        "transactions": [
            {
                "tx_id": "SAR-E2E-TX-1",
                "timestamp": "03/01/2024 10:00:00",
                "amount_usd": 5000.0,
                "origin_account": "XXXXXX1001",
                "destination_account": "XXXXXX2001",
                "location": "Miami, FL",
                "product_type": "Cash deposits",
                "instrument_type": "U.S. Currency",
                "notes": "Cash deposit then immediate transfer.",
            },
            {
                "tx_id": "SAR-E2E-TX-2",
                "timestamp": "03/03/2024 11:30:00",
                "amount_usd": 3000.0,
                "origin_account": "XXXXXX1001",
                "destination_account": "XXXXXX2002",
                "location": "Miami, FL",
                "product_type": "Funds transfer",
                "instrument_type": "Funds transfer",
                "notes": "Second transfer below threshold.",
            },
        ],
        "data_sources": ["core_banking_system"],
    }

    ctr_case = {
        "case_id": "CASE-CTR-E2E-001",
        "subject": {
            "subject_id": "SUBJ-CTR-001",
            "name": "Threshold Test Subject",
            "type": "Individual",
            "country": "US",
        },
        "institution": {
            "name": "Example Community Bank",
            "branch_city": "New York",
            "branch_state": "NY",
            "contact_officer": "Case Analyst",
            "contact_phone": "+1-212-555-2000",
            "primary_federal_regulator": "FDIC",
        },
        "transactions": [
            {
                "tx_id": "CTR-E2E-TX-1",
                "timestamp": "03/04/2024 09:15:00",
                "amount_usd": 12000.0,
                "origin_account": "XXXXXX3001",
                "destination_account": "XXXXXX3001",
                "location": "New York, NY",
                "product_type": "Cash deposits",
                "instrument_type": "U.S. Currency",
                "notes": "Single reportable cash deposit.",
            }
        ],
        "data_sources": ["core_banking_system"],
    }

    return sar_case, ctr_case


def evaluate_case(label: str, status_payload: Dict[str, Any], expected_type: str) -> Dict[str, Any]:
    result = status_payload.get("result") or {}
    router = result.get("router") or {}
    report_types = router.get("report_types") or []
    if not isinstance(report_types, list):
        report_types = []

    agg_by_type = result.get("aggregator_by_type") or {}
    if not isinstance(agg_by_type, dict):
        agg_by_type = {}

    aggregate = agg_by_type.get(expected_type) or result.get("aggregator") or {}
    if not isinstance(aggregate, dict):
        aggregate = {}

    narrative = result.get("narrative") or {}
    if not isinstance(narrative, dict):
        narrative = {}

    narrative_required = bool(aggregate.get("narrative_required"))
    narrative_generated = bool(narrative.get("narrative_text"))

    checks = {
        "job_status_completed": status_payload.get("status") == "completed",
        "router_contains_expected_type": expected_type in report_types,
        "aggregator_by_type_contains_expected": expected_type in agg_by_type,
        "narrative_gate_correct": (narrative_generated if narrative_required else (not narrative_generated)),
    }

    return {
        "label": label,
        "status": status_payload.get("status"),
        "error": status_payload.get("error"),
        "report_types": report_types,
        "aggregator_by_type_keys": sorted(agg_by_type.keys()),
        "narrative_required": narrative_required,
        "narrative_generated": narrative_generated,
        "checks": checks,
    }


def main() -> int:
    print(f"Using backend: {BASE_URL}")
    health_url = BASE_URL.replace("/api/v1", "") + "/health"
    try:
        health = _request_json("GET", health_url)
        print("Health:", json.dumps(health))
    except Exception as exc:
        print(f"Health check failed: {exc}")
        return 1

    sar_case, ctr_case = build_test_cases()

    try:
        print("\nSubmitting SAR-only case...")
        sar_job_id = submit_case(sar_case)
        print("SAR job_id:", sar_job_id)

        print("Submitting CTR-only case...")
        ctr_job_id = submit_case(ctr_case)
        print("CTR job_id:", ctr_job_id)
    except Exception as exc:
        print(f"\nSubmission failed: {exc}")
        print("Hint: /reports/submit requires DB job storage to be healthy.")
        return 1

    try:
        sar_status = wait_for_job(sar_job_id)
        ctr_status = wait_for_job(ctr_job_id)
    except Exception as exc:
        print(f"\nPolling failed: {exc}")
        return 1

    sar_eval = evaluate_case("SAR_ONLY", sar_status, "SAR")
    ctr_eval = evaluate_case("CTR_ONLY", ctr_status, "CTR")

    print("\nRESULTS")
    print(json.dumps({"sar": sar_eval, "ctr": ctr_eval}, indent=2))

    all_checks = list(sar_eval["checks"].values()) + list(ctr_eval["checks"].values())
    if all(all_checks):
        print("\nPASS: End-to-end checks succeeded.")
        return 0

    print("\nFAIL: One or more checks failed.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
