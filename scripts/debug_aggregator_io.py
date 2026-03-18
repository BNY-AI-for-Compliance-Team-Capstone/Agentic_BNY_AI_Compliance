#!/usr/bin/env python3
"""Inspect aggregator input/output from CLI."""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any, Dict, List
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agents.aggregator_agent import AggregatorOrchestrator
from backend.tools.field_mapper import determine_report_types, normalize_case_data


def _load_case(path: str, index: int) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise ValueError(f"Case list is empty: {path}")
        if index < 0 or index >= len(payload):
            raise ValueError(f"Index {index} out of range for list of {len(payload)}")
        payload = payload[index]
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object payload, got {type(payload).__name__}")
    return payload


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug Aggregator agent input/output")
    parser.add_argument("--case", default="data/CASE-2025-380469.json", help="Path to case JSON")
    parser.add_argument("--index", type=int, default=0, help="Record index when --case is a list")
    parser.add_argument("--report-type", choices=["SAR", "CTR", "AUTO"], default="AUTO", help="Force report type or auto-determine")
    parser.add_argument("--out-dir", default="/tmp", help="Directory to save debug artifacts")
    args = parser.parse_args()

    raw_case = _load_case(args.case, args.index)
    normalized = normalize_case_data(raw_case)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_id = str(normalized.get("case_id") or "UNKNOWN")
    normalized_path = out_dir / f"aggregator_input_{case_id}.json"
    _save_json(normalized_path, normalized)

    if args.report_type == "AUTO":
        report_types: List[str] = determine_report_types(normalized)
    else:
        report_types = [args.report_type]

    print("=== Aggregator Input ===")
    print("case_file:", args.case)
    print("case_index:", args.index)
    print("case_id:", case_id)
    print("report_types:", report_types)
    print("normalized_input_saved:", normalized_path)

    if not report_types:
        print("No report type inferred. Nothing to aggregate.")
        return 0

    aggregator = AggregatorOrchestrator()

    for report_type in report_types:
        print(f"\n=== Running Aggregator ({report_type}) ===")
        try:
            output = aggregator.process(raw_data=normalized, case_id=case_id, report_type=report_type)
            out_dict = output.model_dump(mode="json")
            out_path = out_dir / f"aggregator_output_{case_id}_{report_type}.json"
            _save_json(out_path, out_dict)
            print("status: success")
            print("output_saved:", out_path)
            print("output_preview:")
            print(json.dumps({
                "report_type": out_dict.get("report_type"),
                "case_id": out_dict.get("case_id"),
                "narrative_required": out_dict.get("narrative_required"),
                "risk_score": out_dict.get("risk_score"),
                "missing_required_fields": out_dict.get("missing_required_fields", [])[:10],
            }, indent=2, ensure_ascii=False))
        except Exception as exc:
            print("status: failed")
            print("error:", str(exc))
            print("traceback:")
            traceback.print_exc()
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
