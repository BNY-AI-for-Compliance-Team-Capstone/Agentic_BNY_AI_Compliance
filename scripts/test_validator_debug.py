#!/usr/bin/env python3
"""CLI validator debug helper.

Submits a case to the backend and prints validator diagnostics so you can see
exactly why validation passed or failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

import requests


def _load_case(path: str, index: int = 0) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise ValueError(f"No records found in list JSON: {path}")
        if index < 0 or index >= len(payload):
            raise ValueError(f"Index {index} out of range for list JSON with {len(payload)} record(s)")
        payload = payload[index]
    if not isinstance(payload, dict):
        raise ValueError(f"Case payload must be a JSON object, got {type(payload).__name__}")
    return payload


def _http_json(method: str, url: str, timeout: int = 20, **kwargs) -> Dict[str, Any]:
    response = requests.request(method=method, url=url, timeout=timeout, **kwargs)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return {"value": data}
    return data


def submit_case(base_url: str, case_data: Dict[str, Any]) -> str:
    payload = {"transaction_data": case_data}
    data = _http_json("POST", f"{base_url}/api/v1/reports/submit", json=payload, timeout=30)
    job_id = str(data.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"Submit response missing job_id: {data}")
    return job_id


def get_status(base_url: str, job_id: str) -> Dict[str, Any]:
    return _http_json("GET", f"{base_url}/api/v1/reports/{job_id}/status", timeout=30)


def _print_validation_details(status_payload: Dict[str, Any]) -> None:
    result = status_payload.get("result") if isinstance(status_payload.get("result"), dict) else {}
    router = result.get("router") if isinstance(result.get("router"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}

    print("\n--- Pipeline Summary ---")
    print("job_id:", status_payload.get("job_id"))
    print("status:", status_payload.get("status"))
    print("current_agent:", status_payload.get("current_agent"))
    print("progress:", status_payload.get("progress"))
    print("report_types:", status_payload.get("report_types") or router.get("report_types") or [])
    if status_payload.get("error"):
        print("error:", status_payload.get("error"))

    if not validation:
        print("\nNo validation payload found. The job may have failed before validator stage.")
        return

    print("\n--- Validator Summary ---")
    print("validator_source:", validation.get("validator_source"))
    print("validator_status:", validation.get("status"))
    print("approval_flag:", validation.get("approval_flag"))
    print("validation_score:", validation.get("validation_score"))
    print("compliance_checks:", validation.get("compliance_checks"))

    violations = validation.get("violations")
    if isinstance(violations, list):
        print(f"violations_count: {len(violations)}")
        if violations:
            print("\nTop violations:")
            for i, item in enumerate(violations[:20], start=1):
                if not isinstance(item, dict):
                    print(f"{i}. {item}")
                    continue
                sev = item.get("severity", "-")
                rid = item.get("rule_id", "-")
                msg = item.get("message", "-")
                print(f"{i:02d}. [{sev}] {rid}: {msg}")
    else:
        print("violations_count: n/a")

    issues = validation.get("issues")
    if isinstance(issues, list) and issues:
        print("\nIssues:")
        for i, issue in enumerate(issues[:20], start=1):
            print(f"{i:02d}. {issue}")

    recs = validation.get("recommendations")
    if isinstance(recs, list) and recs:
        print("\nRecommendations:")
        for i, rec in enumerate(recs[:20], start=1):
            print(f"{i:02d}. {rec}")

    report_text = validation.get("validation_report")
    if isinstance(report_text, str) and report_text.strip():
        print("\n--- Raw Validation Report ---")
        print(report_text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit and debug validator execution")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001", help="Backend base URL")
    parser.add_argument("--case", default="data/CASE-2025-380469.json", help="Path to case JSON")
    parser.add_argument("--index", type=int, default=0, help="Index when --case is a JSON list")
    parser.add_argument("--job-id", default="", help="Existing job_id to inspect instead of submitting")
    parser.add_argument("--wait-seconds", type=int, default=180, help="Max wait for completion")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Polling interval")
    parser.add_argument("--save", default="", help="Optional output path for full status JSON")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    try:
        health = _http_json("GET", f"{base_url}/health", timeout=8)
        print("Health:", json.dumps(health, ensure_ascii=False))
    except Exception as exc:
        print(f"Health check failed at {base_url}/health: {exc}")
        return 2

    job_id = args.job_id.strip()
    if not job_id:
        case_data = _load_case(args.case, index=args.index)
        print(f"Submitting case: {args.case} (index={args.index})")
        job_id = submit_case(base_url, case_data)
        print("job_id:", job_id)
    else:
        print("Using existing job_id:", job_id)

    deadline = time.time() + max(args.wait_seconds, 1)
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        latest = get_status(base_url, job_id)
        status = str(latest.get("status") or "")
        progress = latest.get("progress")
        current_agent = latest.get("current_agent")
        print(f"poll status={status} progress={progress} current_agent={current_agent}")

        if status in {"completed", "failed"}:
            break
        time.sleep(max(args.poll_seconds, 0.2))

    if not latest:
        print("No status payload received.")
        return 3

    if args.save:
        Path(args.save).write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Saved full status JSON to", args.save)
    else:
        auto_path = Path(f"/tmp/validator_debug_{job_id}.json")
        auto_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Saved full status JSON to", auto_path)

    _print_validation_details(latest)

    final_status = str(latest.get("status") or "")
    if final_status == "failed":
        return 4

    validation = (latest.get("result") or {}).get("validation") if isinstance(latest.get("result"), dict) else {}
    if isinstance(validation, dict) and validation.get("approval_flag") is False:
        return 5

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.HTTPError as exc:
        print("HTTP error:", exc)
        try:
            print("response:", exc.response.text)
        except Exception:
            pass
        raise
    except Exception as exc:
        print("Fatal error:", exc)
        raise
