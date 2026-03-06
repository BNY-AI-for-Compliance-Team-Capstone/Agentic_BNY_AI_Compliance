"""Ingest SAR narrative example JSON into Weaviate Narratives collection.

Usage:
  python scripts/ingest_narrative_examples.py \
    --input knowledge_base/narratives/sar_narrative_examples.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config.settings import settings
from backend.knowledge_base.weaviate_client import WeaviateClient


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _get_total_amount(amounts: Dict[str, Any]) -> float:
    if not isinstance(amounts, dict):
        return 0.0
    if "total_usd" in amounts:
        return _to_float(amounts.get("total_usd"))
    for key in ("cash_deposits", "wire_transfers", "wire_transfers_out", "money_orders_purchased"):
        if key in amounts:
            return _to_float(amounts.get(key))
    return 0.0


def _build_narratives(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    last_updated = str(payload.get("last_updated", date.today().isoformat()))
    examples = payload.get("examples", [])
    if not isinstance(examples, list):
        examples = []

    narratives: List[Dict[str, Any]] = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        example_id = str(item.get("example_id", "")).strip()
        narrative_text = str(item.get("narrative_text", "")).strip()
        if not example_id or not narrative_text:
            continue

        activity_types = _as_list(item.get("activity_types"))
        activity_type = ",".join(activity_types[:3]) if activity_types else "suspicious_activity"
        institution_type = str(item.get("filing_institution_type", "")).strip()
        why_sufficient = str(item.get("why_sufficient", "")).strip()
        key_features = _as_list(item.get("key_features"))

        summary_parts = [f"example_id={example_id}"]
        if institution_type:
            summary_parts.append(f"institution={institution_type}")
        if activity_types:
            summary_parts.append(f"activities={','.join(activity_types)}")
        if why_sufficient:
            summary_parts.append(f"why={why_sufficient}")
        if key_features:
            summary_parts.append(f"features={','.join(key_features[:5])}")
        summary = " | ".join(summary_parts)

        amounts = item.get("amounts_involved", {})
        total_amount = _get_total_amount(amounts if isinstance(amounts, dict) else {})
        transaction_count = 0
        if isinstance(amounts, dict):
            try:
                transaction_count = int(float(amounts.get("transaction_count", 0) or 0))
            except Exception:
                transaction_count = 0

        words = [word for word in narrative_text.split() if word]
        quality = 9.5 if str(item.get("narrative_quality", "")).lower() == "sufficient_complete" else 7.0

        narratives.append(
            {
                "text": narrative_text,
                "summary": summary,
                "activity_type": activity_type[:200],
                "report_type": "SAR",
                "quality_score": quality,
                "word_count": len(words),
                "transaction_count": transaction_count,
                "total_amount": total_amount,
                "date_added": last_updated,
            }
        )
    return narratives


def _exists_by_summary(client: WeaviateClient, summary: str) -> bool:
    response = (
        client.client.query.get("Narratives", ["summary"])
        .with_where({"path": ["summary"], "operator": "Equal", "valueText": summary})
        .with_limit(1)
        .do()
    )
    rows = response.get("data", {}).get("Get", {}).get("Narratives", [])
    return len(rows) > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest SAR narrative examples into Weaviate")
    parser.add_argument(
        "--input",
        default="knowledge_base/narratives/sar_narrative_examples.json",
        help="Path to narrative examples JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print record count without writing to Weaviate",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert all records even if summary already exists",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}")
        return 1

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: invalid JSON in {input_path}: {exc}")
        return 1

    if not isinstance(payload, dict):
        print("ERROR: expected top-level JSON object")
        return 1

    narratives = _build_narratives(payload)
    if not narratives:
        print("ERROR: no narrative examples generated from input")
        return 1

    print(f"Prepared {len(narratives)} narrative examples from {input_path}")
    if args.dry_run:
        print("Dry run enabled. Nothing was written to Weaviate.")
        return 0

    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY is required for embeddings.")
        return 1

    try:
        client = WeaviateClient(settings.WEAVIATE_URL, settings.WEAVIATE_API_KEY)
        client.create_schema()
        created = 0
        skipped = 0
        for idx, narrative in enumerate(narratives, start=1):
            if not args.force and _exists_by_summary(client, narrative["summary"]):
                skipped += 1
                print(f"[{idx}/{len(narratives)}] skipped duplicate {narrative['summary'].split('|')[0].strip()}")
                continue
            object_id = client.add_narrative(narrative)
            created += 1
            print(f"[{idx}/{len(narratives)}] added id={object_id} {narrative['summary'].split('|')[0].strip()}")
    except Exception as exc:
        print(f"ERROR: failed to ingest narratives into Weaviate: {exc}")
        return 1

    print(f"Done. Inserted {created} narrative examples. Skipped {skipped} duplicates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
