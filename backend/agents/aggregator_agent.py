from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import requests
from crewai import Agent, LLM, Task
from loguru import logger
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.tools.field_mapper import normalize_case_data


class TransactionDetail(BaseModel):
    transaction_id: str
    date: datetime
    amount: float
    currency: str
    type: Literal["deposit", "withdrawal", "transfer", "wire"]
    counterparty: Optional[str] = None
    account: str
    description: Optional[str] = None


class RiskFlag(BaseModel):
    flag_type: str
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    threshold_breached: Optional[str] = None
    rule_reference: str


class SARCaseSchema(BaseModel):
    report_type: Literal["SAR"] = "SAR"
    case_id: str

    customer_name: str
    customer_id: str
    account_numbers: List[str] = Field(default_factory=list)
    customer_address: Optional[str] = None
    customer_dob: Optional[str] = None
    customer_ssn: Optional[str] = None

    subject: Dict[str, Any] = Field(default_factory=dict)
    institution: Dict[str, Any] = Field(default_factory=dict)
    SuspiciousActivityInformation: Dict[str, Any] = Field(default_factory=dict)

    suspicious_activity_type: List[str] = Field(default_factory=list)
    activity_date_range: Dict[str, Optional[str]] = Field(
        default_factory=lambda: {"start": None, "end": None}
    )
    total_amount_involved: float = 0.0
    suspicious_activity_description: Optional[str] = None

    transactions: List[TransactionDetail] = Field(default_factory=list)
    transaction_count: int = 0

    risk_flags: List[RiskFlag] = Field(default_factory=list)
    risk_score: float = 0.0

    missing_required_fields: List[str] = Field(default_factory=list)
    data_quality_issues: List[str] = Field(default_factory=list)

    narrative_required: bool = True
    narrative_justification: Optional[str] = None
    data_sources: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CTRCaseSchema(BaseModel):
    report_type: Literal["CTR"] = "CTR"
    case_id: str

    subject: Dict[str, Any] = Field(default_factory=dict)
    institution: Dict[str, Any] = Field(default_factory=dict)
    section_a: Dict[str, Any] = Field(default_factory=dict)
    section_b: Dict[str, Any] = Field(default_factory=dict)
    transaction: Dict[str, Any] = Field(default_factory=dict)
    filing_type: str = "initial"
    financial_institution: Dict[str, Any] = Field(default_factory=dict)
    person_a: Dict[str, Any] = Field(default_factory=dict)

    transactions: List[TransactionDetail] = Field(default_factory=list)
    transaction_count: int = 0
    total_amount_involved: float = 0.0

    risk_flags: List[RiskFlag] = Field(default_factory=list)
    risk_score: float = 0.0

    missing_required_fields: List[str] = Field(default_factory=list)
    data_quality_issues: List[str] = Field(default_factory=list)

    narrative_required: bool = False
    narrative_justification: Optional[str] = None
    data_sources: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SchemaBundle:
    report_type: str
    json_schema: Dict[str, Any]
    narrative_required: Optional[bool]


class SupabaseReportSchemaProvider:
    """Load report schema from Supabase report_types.json_schema."""

    LOCAL_SCHEMA_PATHS = {
        "SAR": Path("knowledge_base/schemas/sar_schema.json"),
        "CTR": Path("knowledge_base/schemas/ctr_schema.json"),
    }

    def __init__(self, base_url: Optional[str] = None, anon_key: Optional[str] = None):
        self.base_url = (base_url or settings.get_supabase_rest_url() or "").rstrip("/")
        self.anon_key = (anon_key or settings.SUPABASE_ANON_KEY or "").strip()

    def _enabled(self) -> bool:
        return bool(self.base_url and self.anon_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _coerce_json(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _load_local_schema(self, report_type: str) -> Dict[str, Any]:
        path = self.LOCAL_SCHEMA_PATHS.get(str(report_type or "").upper())
        if not path or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning("Failed reading local schema {}: {}", path, exc)
            return {}

    def load(self, report_type: str) -> SchemaBundle:
        code = (report_type or "SAR").upper()
        if not self._enabled():
            logger.warning(
                "Supabase REST is not configured; using empty schema for report_type={}",
                code,
            )
            return SchemaBundle(
                report_type=code,
                json_schema=self._load_local_schema(code),
                narrative_required=None,
            )

        url = f"{self.base_url}/rest/v1/report_types"
        query_attempts = [
            {
                "select": "report_type,json_schema,narrative_required",
                "report_type": f"eq.{code}",
                "limit": "1",
            },
            {
                "select": "report_type,json_schema",
                "report_type": f"eq.{code}",
                "limit": "1",
            },
            {
                "select": "report_type_code,json_schema,narrative_required",
                "report_type_code": f"eq.{code}",
                "limit": "1",
            },
            {
                "select": "report_type_code,json_schema",
                "report_type_code": f"eq.{code}",
                "limit": "1",
            },
        ]

        row: Dict[str, Any] = {}
        last_error: Optional[Exception] = None
        for params in query_attempts:
            try:
                response = requests.get(
                    url, headers=self._headers(), params=params, timeout=5
                )
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_error = exc
                # Column mismatch on Supabase returns 400; try next query shape.
                if response.status_code == 400:
                    continue
                break
            except requests.RequestException as exc:
                # Network or timeout error: fail fast and use fallback schema.
                last_error = exc
                break

            payload = response.json()
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                row = payload[0]
                break

        if not row:
            logger.warning(
                "Supabase schema lookup unavailable for report_type={} ({}). Falling back to local required-field defaults.",
                code,
                last_error or "not found",
            )
            return SchemaBundle(
                report_type=code,
                json_schema=self._load_local_schema(code),
                narrative_required=None,
            )

        narrative_required = row.get("narrative_required")
        if not isinstance(narrative_required, bool):
            narrative_required = None
        return SchemaBundle(
            report_type=code,
            json_schema=self._coerce_json(row.get("json_schema")),
            narrative_required=narrative_required,
        )


class TransactionMapper:
    @staticmethod
    def parse_datetime(raw_value: Any) -> datetime:
        if isinstance(raw_value, datetime):
            return raw_value
        text = str(raw_value or "").strip()
        formats = (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.now(timezone.utc)

    @staticmethod
    def infer_type(
        tx: Dict[str, Any],
    ) -> Literal["deposit", "withdrawal", "transfer", "wire"]:
        text = " ".join(
            [
                str(tx.get("type", "") or ""),
                str(tx.get("product_type", "") or ""),
                str(tx.get("instrument_type", "") or ""),
                str(tx.get("description", "") or ""),
                str(tx.get("notes", "") or ""),
            ]
        ).lower()
        if "wire" in text:
            return "wire"
        if "withdraw" in text:
            return "withdrawal"
        if "deposit" in text or "cash" in text:
            return "deposit"
        return "transfer"

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(str(value or 0).replace(",", "").replace("$", "").strip() or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _location_text(tx: Dict[str, Any]) -> str:
        location = tx.get("location")
        if isinstance(location, str):
            return location
        if isinstance(location, dict):
            city = str(location.get("city") or "").strip()
            state = str(location.get("state") or "").strip()[:2]
            if city or state:
                return ", ".join(part for part in [city, state] if part)
            address = str(location.get("address") or "").strip()
            if address:
                parts = [part.strip() for part in address.split(",") if part.strip()]
                if len(parts) >= 2:
                    city = parts[-2]
                    state = parts[-1].split(" ")[0][:2]
                    return ", ".join(part for part in [city, state] if part)
                return address
        return ""

    @staticmethod
    def normalize_transactions(raw_case: Dict[str, Any]) -> List[TransactionDetail]:
        raw_transactions = raw_case.get("transactions", [])
        if not isinstance(raw_transactions, list):
            return []

        normalized: List[TransactionDetail] = []
        for index, tx in enumerate(raw_transactions, start=1):
            if not isinstance(tx, dict):
                continue
            location_text = TransactionMapper._location_text(tx)
            description_text = str(tx.get("description") or tx.get("notes") or "").strip()
            if not description_text and location_text:
                description_text = location_text
            normalized.append(
                TransactionDetail(
                    transaction_id=str(
                        tx.get("transaction_id") or tx.get("tx_id") or f"TX-{index:04d}"
                    ),
                    date=TransactionMapper.parse_datetime(
                        tx.get("date") or tx.get("timestamp") or tx.get("datetime")
                    ),
                    amount=TransactionMapper._to_float(tx.get("amount") or tx.get("amount_usd")),
                    currency=str(tx.get("currency") or "USD"),
                    type=TransactionMapper.infer_type(tx),
                    counterparty=(
                        str(
                            tx.get("counterparty")
                            or tx.get("destination_account")
                            or tx.get("beneficiary_account")
                            or ""
                        ).strip()
                        or None
                    ),
                    account=str(
                        tx.get("account")
                        or tx.get("origin_account")
                        or tx.get("account_number")
                        or ""
                    ).strip(),
                    description=description_text or None,
                )
            )
        return normalized

    @staticmethod
    def extract_accounts(
        raw_case: Dict[str, Any], transactions: List[TransactionDetail]
    ) -> List[str]:
        accounts: List[str] = []
        seen = set()

        provided = raw_case.get("accounts")
        if isinstance(provided, list):
            for item in provided:
                if isinstance(item, dict):
                    value = str(item.get("account_number") or item.get("account") or "").strip()
                else:
                    value = str(item or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    accounts.append(value)

        for tx in transactions:
            for value in [tx.account, tx.counterparty]:
                if value and value not in seen:
                    seen.add(value)
                    accounts.append(value)
        return accounts


class SchemaRequiredFields:
    SAR_FALLBACK = [
        "report_type",
        "case_id",
        "customer_name",
        "customer_id",
        "account_numbers",
        "suspicious_activity_type",
        "activity_date_range.start",
        "activity_date_range.end",
        "total_amount_involved",
        "transactions",
    ]
    CTR_FALLBACK = [
        "report_type",
        "case_id",
        "total_amount_involved",
        "transactions",
    ]

    @staticmethod
    def _extract_json_schema_required(
        node: Any,
        prefix: str,
        definitions: Dict[str, Any],
        visited_refs: set[str],
    ) -> List[str]:
        if not isinstance(node, dict):
            return []

        output: List[str] = []

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            if ref not in visited_refs:
                visited_refs.add(ref)
                resolved = definitions.get(ref.split("/")[-1], {})
                output.extend(
                    SchemaRequiredFields._extract_json_schema_required(
                        resolved,
                        prefix,
                        definitions,
                        visited_refs,
                    )
                )
            return output

        if node.get("type") == "array":
            output.extend(
                SchemaRequiredFields._extract_json_schema_required(
                    node.get("items"),
                    prefix,
                    definitions,
                    visited_refs,
                )
            )

        properties = node.get("properties")
        required = node.get("required")
        if isinstance(properties, dict):
            if isinstance(required, list):
                for field in required:
                    if not isinstance(field, str):
                        continue
                    path = f"{prefix}.{field}" if prefix else field
                    output.append(path)
                    child = properties.get(field)
                    output.extend(
                        SchemaRequiredFields._extract_json_schema_required(
                            child,
                            path,
                            definitions,
                            visited_refs,
                        )
                    )
            for field, child in properties.items():
                child_prefix = f"{prefix}.{field}" if prefix else field
                output.extend(
                    SchemaRequiredFields._extract_json_schema_required(
                        child,
                        child_prefix,
                        definitions,
                        visited_refs,
                    )
                )
        return output

    @staticmethod
    def extract(schema: Dict[str, Any], report_type: str) -> List[str]:
        direct_required = schema.get("required_fields")
        if isinstance(direct_required, list) and direct_required:
            return [str(item) for item in direct_required if str(item).strip()]

        definitions = schema.get("definitions")
        if not isinstance(definitions, dict):
            definitions = {}
        payload_schema = schema.get("input_payload_schema")
        root = payload_schema if isinstance(payload_schema, dict) else schema
        required_paths = SchemaRequiredFields._extract_json_schema_required(
            root,
            "",
            definitions,
            set(),
        )
        deduped = sorted({path for path in required_paths if path})
        if deduped:
            return deduped
        return (
            SchemaRequiredFields.SAR_FALLBACK.copy()
            if report_type.upper() == "SAR"
            else SchemaRequiredFields.CTR_FALLBACK.copy()
        )


class MissingValueReviewer:
    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, dict)):
            return len(value) == 0
        return False

    @staticmethod
    def _get_path_value(data: Dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @staticmethod
    def review(data: Dict[str, Any], required_paths: List[str]) -> List[str]:
        missing: List[str] = []
        for path in required_paths:
            value = MissingValueReviewer._get_path_value(data, path)
            if MissingValueReviewer._is_missing(value):
                missing.append(path)
        return missing


class SARRuleEngine:
    STRUCTURING_THRESHOLD = 10_000.0
    HIGH_VALUE_THRESHOLD = 50_000.0
    SEVERITY_WEIGHTS = {"low": 10, "medium": 25, "high": 50, "critical": 100}

    @staticmethod
    def _extract_alerts(raw_case: Dict[str, Any]) -> List[Dict[str, Any]]:
        alerts = raw_case.get("alerts")
        if isinstance(alerts, list):
            return [item for item in alerts if isinstance(item, dict)]
        single_alert = raw_case.get("alert")
        if isinstance(single_alert, dict):
            return [single_alert]

        red_flags = raw_case.get("red_flags")
        if isinstance(red_flags, list) and red_flags:
            converted: List[Dict[str, Any]] = []
            for item in red_flags:
                if not isinstance(item, dict):
                    continue
                converted.append(
                    {
                        "subtype": item.get("indicator"),
                        "description": item.get("description"),
                        "severity": item.get("severity") or "medium",
                        "rule_id": item.get("regulatory_reference"),
                    }
                )
            if converted:
                return converted
        return []

    @staticmethod
    def _collect_suspicious_types(
        raw_case: Dict[str, Any], alerts: List[Dict[str, Any]]
    ) -> List[str]:
        suspicious_types: List[str] = []
        sai = raw_case.get("SuspiciousActivityInformation", {})
        if isinstance(sai, dict):
            category_map = {
                "29_Structuring": "structuring",
                "30_TerroristFinancing": "terrorist financing",
                "31_Fraud": "fraud",
                "33_MoneyLaundering": "money laundering",
                "35_OtherSuspiciousActivities": "other suspicious activity",
                "38_MortgageFraud": "mortgage fraud",
            }
            for key, label in category_map.items():
                values = sai.get(key)
                if isinstance(values, list) and values:
                    suspicious_types.append(label)

        part2 = raw_case.get("part_2_suspicious_activity") if isinstance(raw_case.get("part_2_suspicious_activity"), dict) else {}
        categories = part2.get("suspicious_activity_categories") if isinstance(part2.get("suspicious_activity_categories"), dict) else {}
        part2_labels = {
            "29_structuring": "structuring",
            "30_terrorist_financing": "terrorist financing",
            "31_fraud": "fraud",
            "33_money_laundering": "money laundering",
            "35_other_suspicious": "other suspicious activity",
            "38_mortgage_fraud": "mortgage fraud",
        }
        for key, values in categories.items():
            if isinstance(values, list) and values:
                suspicious_types.append(part2_labels.get(str(key), str(key)))

        for alert in alerts:
            subtype = str(alert.get("subtype") or "").strip()
            if subtype:
                suspicious_types.append(subtype)
            red_flags = alert.get("red_flags")
            if isinstance(red_flags, list):
                suspicious_types.extend(
                    [str(item) for item in red_flags if str(item).strip()]
                )

        # Preserve order while removing duplicates.
        deduped: List[str] = []
        seen = set()
        for value in suspicious_types:
            lowered = value.lower()
            if lowered not in seen:
                seen.add(lowered)
                deduped.append(value)
        return deduped

    @staticmethod
    def _build_activity_date_range(
        raw_case: Dict[str, Any], transactions: List[TransactionDetail]
    ) -> Dict[str, Optional[str]]:
        direct_range = raw_case.get("activity_date_range")
        if isinstance(direct_range, dict):
            start = direct_range.get("start")
            end = direct_range.get("end")
            if start or end:
                return {
                    "start": str(start) if start else None,
                    "end": str(end) if end else None,
                }

        sai = raw_case.get("SuspiciousActivityInformation", {})
        if isinstance(sai, dict):
            date_block = sai.get("27_DateOrDateRange")
            if isinstance(date_block, dict):
                start = date_block.get("from")
                end = date_block.get("to")
                if start or end:
                    return {
                        "start": str(start) if start else None,
                        "end": str(end) if end else None,
                    }

        if not transactions:
            return {"start": None, "end": None}
        dates = sorted(tx.date for tx in transactions)
        return {
            "start": dates[0].strftime("%Y-%m-%d"),
            "end": dates[-1].strftime("%Y-%m-%d"),
        }

    @staticmethod
    def _build_suspicious_activity_block(
        raw_case: Dict[str, Any],
        suspicious_types: List[str],
        total_amount: float,
        date_range: Dict[str, Optional[str]],
    ) -> Dict[str, Any]:
        existing = raw_case.get("SuspiciousActivityInformation")
        if isinstance(existing, dict) and existing:
            merged = dict(existing)
            merged.setdefault(
                "26_AmountInvolved", {"amount_usd": total_amount, "no_amount": False}
            )
            merged.setdefault(
                "27_DateOrDateRange",
                {"from": date_range.get("start"), "to": date_range.get("end")},
            )
            return merged

        lowered = " ".join(suspicious_types).lower()
        return {
            "26_AmountInvolved": {"amount_usd": total_amount, "no_amount": False},
            "27_DateOrDateRange": {
                "from": date_range.get("start"),
                "to": date_range.get("end"),
            },
            "29_Structuring": suspicious_types if "structur" in lowered else [],
            "30_TerroristFinancing": suspicious_types if "terror" in lowered else [],
            "31_Fraud": suspicious_types if "fraud" in lowered else [],
            "33_MoneyLaundering": suspicious_types if "launder" in lowered else [],
            "35_OtherSuspiciousActivities": suspicious_types,
            "39_ProductTypesInvolved": [],
            "40_InstrumentTypesInvolved": [],
        }

    @staticmethod
    def map_fields(
        raw_case: Dict[str, Any], case_id: Optional[str] = None
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        transactions = TransactionMapper.normalize_transactions(raw_case)
        alerts = SARRuleEngine._extract_alerts(raw_case)
        suspicious_types = SARRuleEngine._collect_suspicious_types(raw_case, alerts)

        subject = (
            raw_case.get("subject") if isinstance(raw_case.get("subject"), dict) else {}
        )
        institution = (
            raw_case.get("institution")
            if isinstance(raw_case.get("institution"), dict)
            else {}
        )

        account_numbers = TransactionMapper.extract_accounts(raw_case, transactions)

        suspicious_info = raw_case.get("SuspiciousActivityInformation")
        total_amount = 0.0
        if isinstance(suspicious_info, dict):
            amount_block = suspicious_info.get("26_AmountInvolved")
            if isinstance(amount_block, dict):
                try:
                    total_amount = float(amount_block.get("amount_usd") or 0.0)
                except (TypeError, ValueError):
                    total_amount = 0.0
            elif suspicious_info.get("28_CumulativeAmount") not in (None, ""):
                try:
                    total_amount = float(str(suspicious_info.get("28_CumulativeAmount")).replace(",", ""))
                except (TypeError, ValueError):
                    total_amount = 0.0

        if total_amount == 0.0:
            total_amount = float(raw_case.get("amount") or raw_case.get("total_amount_involved") or 0.0)
        if total_amount == 0.0:
            total_amount = float(sum(tx.amount for tx in transactions))

        activity_date_range = SARRuleEngine._build_activity_date_range(raw_case, transactions)
        suspicious_activity_block = SARRuleEngine._build_suspicious_activity_block(
            raw_case=raw_case,
            suspicious_types=suspicious_types,
            total_amount=total_amount,
            date_range=activity_date_range,
        )

        customer_name = str(
            subject.get("name")
            or " ".join(
                part
                for part in [subject.get("first_name"), subject.get("last_name")]
                if str(part or "").strip()
            )
            or "UNKNOWN"
        )

        customer_id = str(
            subject.get("subject_id")
            or subject.get("customer_id")
            or subject.get("id")
            or f"CUS-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )

        suspicious_desc_parts: List[str] = []
        for item in suspicious_types:
            text_value = str(item or "").strip()
            if text_value:
                suspicious_desc_parts.append(text_value)
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            desc = str(alert.get("description") or "").strip()
            if desc:
                suspicious_desc_parts.append(desc)
        suspicious_activity_description = "; ".join(
            list(dict.fromkeys(suspicious_desc_parts))[:8]
        ) or None

        mapped = {
            "report_type": "SAR",
            "case_id": str(
                case_id
                or raw_case.get("case_id")
                or f"SAR-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            ),
            "customer_name": customer_name,
            "customer_id": customer_id,
            "account_numbers": account_numbers,
            "customer_address": str(subject.get("address") or "").strip() or None,
            "customer_dob": str(subject.get("date_of_birth") or subject.get("dob") or "").strip() or None,
            "customer_ssn": str(
                subject.get("ssn_or_ein")
                or subject.get("ssn")
                or subject.get("ein")
                or subject.get("tin")
                or ""
            ).strip()
            or None,
            "subject": subject,
            "institution": institution,
            "SuspiciousActivityInformation": suspicious_activity_block,
            "suspicious_activity_type": suspicious_types,
            "activity_date_range": activity_date_range,
            "total_amount_involved": total_amount,
            "suspicious_activity_description": suspicious_activity_description,
            "transactions": transactions,
            "transaction_count": len(transactions),
            "risk_flags": [],
            "risk_score": 0.0,
            "missing_required_fields": [],
            "data_quality_issues": [],
            "narrative_required": True,
            "narrative_justification": None,
            "data_sources": (
                raw_case.get("data_sources", [])
                if isinstance(raw_case.get("data_sources"), list)
                else []
            ),
            "created_at": datetime.now(timezone.utc),
        }
        return mapped, alerts


    @staticmethod
    def detect_risks(
        transactions: List[TransactionDetail],
        alerts: List[Dict[str, Any]],
        suspicious_types: List[str],
        total_amount: float,
    ) -> tuple[List[RiskFlag], float]:
        flags: List[RiskFlag] = []

        sub_threshold = [
            tx
            for tx in transactions
            if tx.amount < SARRuleEngine.STRUCTURING_THRESHOLD
            and tx.type in {"deposit", "wire", "transfer"}
        ]
        if (
            len(sub_threshold) >= 3
            and sum(tx.amount for tx in sub_threshold)
            > SARRuleEngine.STRUCTURING_THRESHOLD
        ):
            flags.append(
                RiskFlag(
                    flag_type="structuring",
                    severity="high",
                    description=(
                        f"{len(sub_threshold)} sub-threshold transactions totaling "
                        f"${sum(tx.amount for tx in sub_threshold):,.2f}"
                    ),
                    threshold_breached="$10,000.00",
                    rule_reference="31 USC 5324",
                )
            )

        if total_amount >= SARRuleEngine.HIGH_VALUE_THRESHOLD:
            flags.append(
                RiskFlag(
                    flag_type="high_value_activity",
                    severity="medium",
                    description=f"Total suspicious amount ${total_amount:,.2f} exceeds $50,000",
                    threshold_breached="$50,000.00",
                    rule_reference="INTERNAL_POLICY_HIGH_VALUE",
                )
            )

        lowered_types = " ".join(suspicious_types).lower()
        if "fraud" in lowered_types:
            flags.append(
                RiskFlag(
                    flag_type="fraud_signal",
                    severity="high",
                    description="Fraud indicators detected in suspicious activity categories",
                    rule_reference="FINCEN_FRAUD_GUIDANCE",
                )
            )
        if "launder" in lowered_types:
            flags.append(
                RiskFlag(
                    flag_type="money_laundering_signal",
                    severity="critical",
                    description="Money laundering indicators detected in suspicious activity categories",
                    rule_reference="BSA_AML_PROGRAM",
                )
            )

        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            description = str(alert.get("description") or "").strip()
            if not description:
                trigger_reasons = alert.get("trigger_reasons")
                if isinstance(trigger_reasons, list):
                    description = "; ".join(
                        str(item) for item in trigger_reasons if str(item).strip()
                    )
            if not description:
                continue
            severity = str(alert.get("severity") or "medium").lower()
            if severity not in SARRuleEngine.SEVERITY_WEIGHTS:
                severity = "medium"
            flags.append(
                RiskFlag(
                    flag_type=str(alert.get("subtype") or "general_alert"),
                    severity=severity,  # type: ignore[arg-type]
                    description=description,
                    rule_reference=str(
                        alert.get("rule_id") or alert.get("alert_id") or "ALERT"
                    ),
                )
            )

        score = min(
            float(
                sum(
                    SARRuleEngine.SEVERITY_WEIGHTS.get(flag.severity, 0)
                    for flag in flags
                )
            ),
            100.0,
        )
        return flags, score


class CTRRuleEngine:
    CTR_THRESHOLD = 10_000.0
    SEVERITY_WEIGHTS = {"low": 10, "medium": 25, "high": 50, "critical": 100}

    @staticmethod
    def map_fields(
        raw_case: Dict[str, Any], case_id: Optional[str] = None
    ) -> Dict[str, Any]:
        transactions = TransactionMapper.normalize_transactions(raw_case)
        subject = (
            raw_case.get("subject") if isinstance(raw_case.get("subject"), dict) else {}
        )
        institution = (
            raw_case.get("institution")
            if isinstance(raw_case.get("institution"), dict)
            else {}
        )
        section_a = (
            raw_case.get("section_a")
            if isinstance(raw_case.get("section_a"), dict)
            else {}
        )
        section_b = (
            raw_case.get("section_b")
            if isinstance(raw_case.get("section_b"), dict)
            else {}
        )
        tx_block = (
            raw_case.get("transaction")
            if isinstance(raw_case.get("transaction"), dict)
            else {}
        )

        fallback_city = ""
        fallback_state = ""
        for tx in transactions:
            location = str(getattr(tx, "description", "") or "")
            if not location and isinstance(raw_case.get("transactions"), list):
                # Re-read original location field from the matching raw transaction when present.
                for raw_tx in raw_case.get("transactions", []):
                    if not isinstance(raw_tx, dict):
                        continue
                    if str(
                        raw_tx.get("tx_id") or raw_tx.get("transaction_id") or ""
                    ) == str(tx.transaction_id):
                        location = str(raw_tx.get("location") or "")
                        break
            if "," in location:
                city, state = location.split(",", 1)
                fallback_city = city.strip() or fallback_city
                fallback_state = state.strip()[:2] or fallback_state
                break

        cash_in = sum(tx.amount for tx in transactions if tx.type == "deposit")
        cash_out = sum(
            tx.amount
            for tx in transactions
            if tx.type in {"withdrawal", "wire", "transfer"}
        )
        computed_tx_defaults = {
            "cash_in": cash_in,
            "cash_out": cash_out,
            "currency_exchange": False,
            "wire_transfer": any(tx.type == "wire" for tx in transactions),
        }

        tx_block = dict(tx_block) if isinstance(tx_block, dict) else {}
        if not tx_block:
            tx_block = dict(computed_tx_defaults)
        else:
            if tx_block.get("cash_in") in (None, ""):
                tx_block["cash_in"] = computed_tx_defaults["cash_in"]
            if tx_block.get("cash_out") in (None, ""):
                tx_block["cash_out"] = computed_tx_defaults["cash_out"]
            if tx_block.get("currency_exchange") in (None, ""):
                tx_block["currency_exchange"] = computed_tx_defaults["currency_exchange"]
            if tx_block.get("wire_transfer") in (None, ""):
                tx_block["wire_transfer"] = computed_tx_defaults["wire_transfer"]

        if not tx_block.get("date"):
            if transactions:
                tx_block["date"] = min(tx.date for tx in transactions).strftime(
                    "%Y-%m-%d"
                )
            else:
                tx_block["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        subject_name = str(subject.get("name") or "").strip()
        section_a_defaults = {
            "last_name": str(subject.get("last_name") or subject_name or "UNKNOWN"),
            "first_name": str(subject.get("first_name") or ""),
            "middle_initial": str(subject.get("middle_initial") or ""),
            "ssn_or_ein": str(
                subject.get("ssn_or_ein")
                or subject.get("ssn")
                or subject.get("ein")
                or subject.get("tin")
                or "UNKNOWN"
            ),
            "address": str(
                subject.get("address")
                or f"{fallback_city}, {fallback_state}".strip(", ")
                or "UNKNOWN"
            ),
            "city": str(subject.get("city") or fallback_city or "UNKNOWN"),
            "state": str(subject.get("state") or fallback_state or "NA")[:2],
            "zip": str(subject.get("zip") or subject.get("postal_code") or "00000"),
            "country": str(subject.get("country") or "US"),
        }
        section_a = dict(section_a) if isinstance(section_a, dict) else {}
        section_a = {
            **section_a_defaults,
            **{k: v for k, v in section_a.items() if str(v or "").strip()},
        }

        provided_fi = (
            raw_case.get("financial_institution")
            if isinstance(raw_case.get("financial_institution"), dict)
            else {}
        )
        default_fi_address = f"{institution.get('branch_city', fallback_city)}, {institution.get('branch_state', fallback_state)}".strip(
            ", "
        )
        financial_institution_defaults = {
            "name": str(institution.get("name") or "UNKNOWN"),
            "address": str(
                institution.get("address") or default_fi_address or "UNKNOWN"
            ),
            "city": str(
                institution.get("city")
                or institution.get("branch_city")
                or fallback_city
                or "UNKNOWN"
            ),
            "state": str(
                institution.get("state")
                or institution.get("branch_state")
                or fallback_state
                or "NA"
            )[:2],
            "zip": str(
                institution.get("zip") or institution.get("postal_code") or "00000"
            ),
            "ein_or_ssn": str(
                institution.get("ein_or_ssn")
                or institution.get("ein")
                or institution.get("tin")
                or "UNKNOWN"
            ),
        }
        financial_institution = {
            **financial_institution_defaults,
            **{k: v for k, v in provided_fi.items() if str(v or "").strip()},
        }

        total_amount = float(tx_block.get("cash_in") or 0.0) + float(
            tx_block.get("cash_out") or 0.0
        )
        if total_amount == 0.0:
            total_amount = float(sum(tx.amount for tx in transactions))

        case_value = str(
            case_id
            or raw_case.get("case_id")
            or f"CTR-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        return {
            "report_type": "CTR",
            "case_id": case_value,
            "filing_type": str(raw_case.get("filing_type") or "initial"),
            "subject": subject,
            "institution": institution,
            "financial_institution": financial_institution,
            "section_a": section_a,
            "person_a": section_a,
            "section_b": section_b or {"blank_reason": "conducted_on_own_behalf"},
            "transaction": tx_block,
            "transactions": transactions,
            "transaction_count": len(transactions),
            "total_amount_involved": total_amount,
            "risk_flags": [],
            "risk_score": 0.0,
            "missing_required_fields": [],
            "data_quality_issues": [],
            "narrative_required": False,
            "narrative_justification": None,
            "data_sources": (
                raw_case.get("data_sources", [])
                if isinstance(raw_case.get("data_sources"), list)
                else []
            ),
            "created_at": datetime.now(timezone.utc),
        }

    @staticmethod
    def detect_risks(case_data: Dict[str, Any]) -> tuple[List[RiskFlag], float]:
        flags: List[RiskFlag] = []
        transaction = case_data.get("transaction", {})
        cash_in = float(transaction.get("cash_in") or 0.0)
        cash_out = float(transaction.get("cash_out") or 0.0)
        total_cash = cash_in + cash_out

        if total_cash > 50_000:
            flags.append(
                RiskFlag(
                    flag_type="large_cash_transaction",
                    severity="medium",
                    description=f"Large cash amount reported (${total_cash:,.2f})",
                    threshold_breached="$50,000.00",
                    rule_reference="INTERNAL_POLICY_HIGH_VALUE",
                )
            )

        if bool(transaction.get("currency_exchange")):
            flags.append(
                RiskFlag(
                    flag_type="currency_exchange",
                    severity="low",
                    description="Currency exchange included in CTR filing",
                    rule_reference="CTR_OPERATIONAL_GUIDANCE",
                )
            )

        if (
            bool(transaction.get("wire_transfer"))
            and total_cash >= CTRRuleEngine.CTR_THRESHOLD
        ):
            flags.append(
                RiskFlag(
                    flag_type="wire_with_cash",
                    severity="medium",
                    description="Wire transfer activity observed with reportable cash movement",
                    threshold_breached="$10,000.00",
                    rule_reference="CTR_WIRE_REVIEW_POLICY",
                )
            )

        score = min(
            float(
                sum(
                    CTRRuleEngine.SEVERITY_WEIGHTS.get(flag.severity, 0)
                    for flag in flags
                )
            ),
            100.0,
        )
        return flags, score


class MultiReportAggregator:
    """Agent 3: deterministic multi-report aggregator (SAR/CTR)."""

    def __init__(self, llm: Optional[LLM] = None):
        # llm retained for backward compatibility with existing constructor calls.
        self.llm = llm
        self.schema_provider = SupabaseReportSchemaProvider()

    @staticmethod
    def _build_quality_issues(report_type: str, data: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        if report_type == "SAR":
            for field in ["customer_ssn", "customer_address", "customer_dob"]:
                if not data.get(field):
                    issues.append(f"Critical field missing: {field}")
            for index, tx in enumerate(data.get("transactions", []), start=1):
                tx_dict = tx if isinstance(tx, dict) else tx.model_dump()
                if not tx_dict.get("counterparty"):
                    issues.append(f"Transaction {index}: counterparty missing")
        else:
            tx_block = data.get("transaction", {})
            cash_in = float(tx_block.get("cash_in") or 0.0)
            cash_out = float(tx_block.get("cash_out") or 0.0)
            if (cash_in + cash_out) < CTRRuleEngine.CTR_THRESHOLD:
                issues.append(
                    "CTR threshold not met based on mapped cash_in/cash_out values"
                )
        return issues

    @staticmethod
    def _narrative_decision(
        report_type: str,
        schema_narrative_required: Optional[bool],
        risk_flags: List[RiskFlag],
        missing_required: List[str],
    ) -> tuple[bool, Optional[str]]:
        if isinstance(schema_narrative_required, bool):
            if schema_narrative_required:
                return True, "Narrative required by report schema configuration"
            return False, "Narrative not required by report schema configuration"

        if report_type == "SAR":
            reasons = ["SAR filing requires narrative explanation"]
            high_risk = [
                flag for flag in risk_flags if flag.severity in {"high", "critical"}
            ]
            if high_risk:
                reasons.append(f"{len(high_risk)} high/critical risk flag(s)")
            if missing_required:
                reasons.append("Data gaps require explanatory narrative")
            return True, "; ".join(reasons)

        return False, None

    def process_sar(
        self, raw_data: Dict[str, Any], case_id: Optional[str] = None
    ) -> SARCaseSchema:
        case = normalize_case_data(raw_data)
        schema = self.schema_provider.load("SAR")
        required_paths = SchemaRequiredFields.extract(schema.json_schema, "SAR")

        mapped, alerts = SARRuleEngine.map_fields(case, case_id=case_id)
        risk_flags, risk_score = SARRuleEngine.detect_risks(
            transactions=mapped["transactions"],
            alerts=alerts,
            suspicious_types=mapped["suspicious_activity_type"],
            total_amount=float(mapped["total_amount_involved"] or 0.0),
        )
        mapped["risk_flags"] = [flag.model_dump() for flag in risk_flags]
        mapped["risk_score"] = risk_score
        mapped["missing_required_fields"] = MissingValueReviewer.review(
            mapped, required_paths
        )
        mapped["data_quality_issues"] = self._build_quality_issues("SAR", mapped)
        narrative_required, justification = self._narrative_decision(
            report_type="SAR",
            schema_narrative_required=schema.narrative_required,
            risk_flags=risk_flags,
            missing_required=mapped["missing_required_fields"],
        )
        mapped["narrative_required"] = narrative_required
        mapped["narrative_justification"] = justification
        return SARCaseSchema(**mapped)

    def process_ctr(
        self, raw_data: Dict[str, Any], case_id: Optional[str] = None
    ) -> CTRCaseSchema:
        case = normalize_case_data(raw_data)
        schema = self.schema_provider.load("CTR")
        required_paths = SchemaRequiredFields.extract(schema.json_schema, "CTR")

        mapped = CTRRuleEngine.map_fields(case, case_id=case_id)
        risk_flags, risk_score = CTRRuleEngine.detect_risks(mapped)
        mapped["risk_flags"] = [flag.model_dump() for flag in risk_flags]
        mapped["risk_score"] = risk_score
        mapped["missing_required_fields"] = MissingValueReviewer.review(
            mapped, required_paths
        )
        mapped["data_quality_issues"] = self._build_quality_issues("CTR", mapped)
        narrative_required, justification = self._narrative_decision(
            report_type="CTR",
            schema_narrative_required=schema.narrative_required,
            risk_flags=risk_flags,
            missing_required=mapped["missing_required_fields"],
        )
        mapped["narrative_required"] = narrative_required
        mapped["narrative_justification"] = justification
        return CTRCaseSchema(**mapped)

    def process(
        self,
        raw_data: Dict[str, Any],
        case_id: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> Union[SARCaseSchema, CTRCaseSchema]:
        case = normalize_case_data(raw_data)
        resolved_report_type = str(
            report_type or case.get("report_type") or "SAR"
        ).upper()
        if resolved_report_type == "SAR":
            return self.process_sar(case, case_id=case_id)
        if resolved_report_type == "CTR":
            return self.process_ctr(case, case_id=case_id)
        raise ValueError(
            f"Unknown report_type: {resolved_report_type}. Expected SAR or CTR."
        )


# Backward-compatible name used by current orchestration code.
AggregatorOrchestrator = MultiReportAggregator


def create_aggregator_agent(llm: LLM, tools: list) -> Agent:
    """Legacy compatibility helper (not used by deterministic orchestrator)."""
    return Agent(
        role="Data Mapper and Case Analyst",
        goal="Map input transactions into schema-aligned SAR/CTR aggregate JSON.",
        backstory=(
            "You are a compliance data analyst focused on deterministic field mapping "
            "and complete, auditable outputs."
        ),
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_aggregator_task(agent: Agent, router_output: dict) -> Task:
    """Legacy compatibility helper (not used by deterministic orchestrator)."""
    return Task(
        description=(
            "Map transaction payload into a report-ready aggregate object.\n\n"
            f"Router output:\n{json.dumps(router_output, indent=2)}"
        ),
        expected_output=(
            "JSON object with mapped report fields, risk flags, completeness checks, and "
            "narrative_required decision."
        ),
        agent=agent,
    )
