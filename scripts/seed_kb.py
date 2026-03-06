"""Seed the knowledge base with initial data."""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.supabase_client import SupabaseClient
from backend.config.settings import settings

SAR_SCHEMA_PATH = ROOT / "knowledge_base" / "schemas" / "sar_schema.json"
CTR_SCHEMA_PATH = ROOT / "knowledge_base" / "schemas" / "ctr_schema.json"


def load_schema(schema_path: Path) -> Dict[str, Any]:
    """Load a report schema JSON file from disk."""
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    if not isinstance(schema, dict):
        raise ValueError(f"Schema file must contain a JSON object: {schema_path}")

    return schema


def seed_database(db: SupabaseClient) -> None:
    db.create_tables()
    schema_paths = [SAR_SCHEMA_PATH, CTR_SCHEMA_PATH]
    added_report_types = []
    for schema_path in schema_paths:
        schema = load_schema(schema_path)
        report_type = schema.get("report_type")
        if not report_type:
            raise ValueError(f"Missing report_type in schema file: {schema_path}")
        version = schema.get("version", "1.0")
        effective_date = schema.get("effective_date", date.today().isoformat())

        db.add_schema(
            report_type=report_type,
            version=version,
            schema_json=schema,
            effective_date=effective_date,
        )
        added_report_types.append(report_type)
        print(f"✓ Added {report_type} schema from {schema_path}")

    rules = [
        {
            "rule_id": "SAR-C001",
            "report_type": "SAR",
            "severity": "critical",
            "rule_json": {
                "condition": "subject.last_name IS NOT NULL OR subject.ssn IS NOT NULL",
                "message": "Subject must have last name or SSN",
            },
        },
        {
            "rule_id": "CTR-C001",
            "report_type": "CTR",
            "severity": "critical",
            "rule_json": {
                "condition": "total_cash_amount >= 10000",
                "message": "CTR requires total cash amount at or above $10,000",
            },
        },
    ]
    for rule in rules:
        if rule["report_type"] in added_report_types:
            db.add_validation_rule(rule)
    print(f"✓ Added {len(rules)} validation rules")


def seed_weaviate(kb: KBManager) -> None:
    narratives = [
        {
            "text": "Customer made eleven cash deposits under $10,000 all within 10 days.",
            "summary": "Multiple cash deposits just below CTR threshold",
            "activity_type": "Structuring",
            "report_type": "SAR",
            "quality_score": 9.0,
            "word_count": 42,
            "transaction_count": 11,
            "total_amount": 98600,
            "date_added": date.today().isoformat(),
        }
    ]

    for narrative in narratives:
        narrative_id = kb.add_narrative_example(narrative)
        print(f"✓ Added narrative {narrative_id}")

    regulations = [
        {
            "text": "The BSA requires filing of a SAR within 30 days of detection.",
            "regulation_name": "BSA",
            "section": "31 CFR 1020.320",
            "effective_date": date.today().isoformat(),
            "source_url": "https://www.fincen.gov/",
        }
    ]

    for regulation in regulations:
        reg_id = kb.add_regulation(regulation)
        print(f"✓ Added regulation {reg_id}")


def main() -> None:
    print("Seeding Knowledge Base...")
    kb = KBManager()
    print("\n1. Seeding database...")
    should_seed_db = settings.has_database_dsn()
    if should_seed_db:
        db = SupabaseClient()
        seed_database(db)
    else:
        print("⚠ Skipping database seed: no DB DSN configured (REST-only mode detected).")
    print("\n2. Seeding Weaviate...")
    seed_weaviate(kb)
    print("\n✅ Knowledge Base seeded successfully!")
    print("\nYou can now run the application with 'uvicorn backend.api.main:app'")


if __name__ == "__main__":
    main()
