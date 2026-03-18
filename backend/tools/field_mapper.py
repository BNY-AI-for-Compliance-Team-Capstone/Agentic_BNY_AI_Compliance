"""Field mapping utilities for SAR and CTR PDF filing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List

TRUE_STRINGS = {"true", "yes", "y", "1", "t"}
FALSE_STRINGS = {"false", "no", "n", "0", "f"}
NULL_STRINGS = {"null", "none", "nil", "na", "n/a"}


def _normalize_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered in NULL_STRINGS:
        return None
    if lowered in TRUE_STRINGS:
        return True
    if lowered in FALSE_STRINGS:
        return False
    return cleaned


def _normalize_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_obj(item) for item in value]
    return _normalize_scalar(value)


def _to_float(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_mmddyyyy(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    probe = text[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(probe, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return probe


def _set_if_missing(payload: Dict[str, Any], path: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    parts = [part for part in path.split(".") if part]
    if not parts:
        return
    current = payload
    for key in parts[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    leaf = parts[-1]
    existing = current.get(leaf)
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        current[leaf] = value


def _location_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        city = str(value.get("city") or "").strip()
        state = str(value.get("state") or "").strip()
        if city or state:
            return ", ".join(part for part in [city, state] if part)
        address = str(value.get("address") or "").strip()
        if address:
            parts = [part.strip() for part in address.split(",") if part.strip()]
            if len(parts) >= 2:
                city = parts[-2]
                state = parts[-1].split(" ")[0][:2]
                return ", ".join(part for part in [city, state] if part)
            return address
    return ""


def _canonicalize_transactions(case: Dict[str, Any]) -> None:
    txs = case.get("transactions")
    if not isinstance(txs, list):
        return

    normalized_txs: List[Dict[str, Any]] = []
    for idx, tx in enumerate(txs, start=1):
        if not isinstance(tx, dict):
            continue
        item = dict(tx)

        if not item.get("tx_id"):
            item["tx_id"] = item.get("transaction_id") or f"TX-{idx:04d}"
        if not item.get("transaction_id"):
            item["transaction_id"] = item.get("tx_id")

        if item.get("amount_usd") in (None, "") and item.get("amount") not in (None, ""):
            item["amount_usd"] = _to_float(item.get("amount"))
        if item.get("amount") in (None, "") and item.get("amount_usd") not in (None, ""):
            item["amount"] = _to_float(item.get("amount_usd"))

        if not item.get("timestamp"):
            date_part = str(item.get("date") or "").strip()
            time_part = str(item.get("time") or "").strip()
            if date_part and time_part:
                item["timestamp"] = f"{date_part} {time_part}"
            elif date_part:
                item["timestamp"] = date_part

        if not item.get("origin_account"):
            item["origin_account"] = item.get("account_number") or item.get("account") or ""
        if not item.get("account"):
            item["account"] = item.get("origin_account")

        location_text = _location_to_text(item.get("location"))
        if location_text:
            item["location"] = location_text

        normalized_txs.append(item)

    case["transactions"] = normalized_txs


def _canonicalize_extended_case(case: Dict[str, Any]) -> Dict[str, Any]:
    report_meta = case.get("report_metadata") if isinstance(case.get("report_metadata"), dict) else {}
    if report_meta:
        _set_if_missing(case, "report_type", report_meta.get("filing_type"))
        _set_if_missing(case, "filing_type", report_meta.get("report_type"))
        _set_if_missing(case, "sar_filing_date", _to_mmddyyyy(report_meta.get("filing_date")))

    part1 = case.get("part_1_subject_information") if isinstance(case.get("part_1_subject_information"), dict) else {}
    if part1:
        personal = part1.get("personal_details") if isinstance(part1.get("personal_details"), dict) else {}
        address = part1.get("address") if isinstance(part1.get("address"), dict) else {}
        ident = part1.get("identification") if isinstance(part1.get("identification"), dict) else {}

        first = str(personal.get("first_name") or "").strip()
        last = str(personal.get("last_name") or "").strip()
        full_name = " ".join(part for part in [first, last] if part)

        _set_if_missing(case, "subject.first_name", first)
        _set_if_missing(case, "subject.last_name", last)
        _set_if_missing(case, "subject.name", full_name)
        _set_if_missing(case, "subject.middle_initial", personal.get("middle_initial"))
        _set_if_missing(case, "subject.date_of_birth", _to_mmddyyyy(personal.get("date_of_birth")))
        _set_if_missing(case, "subject.dob", _to_mmddyyyy(personal.get("date_of_birth")))
        _set_if_missing(case, "subject.industry_or_occupation", personal.get("occupation"))
        _set_if_missing(case, "subject.occupation", personal.get("occupation"))

        _set_if_missing(case, "subject.address", address.get("street"))
        _set_if_missing(case, "subject.city", address.get("city"))
        _set_if_missing(case, "subject.state", address.get("state"))
        _set_if_missing(case, "subject.zip", address.get("zip_code"))
        _set_if_missing(case, "subject.country", address.get("country_code"))

        _set_if_missing(case, "subject.tin", ident.get("tin"))
        _set_if_missing(case, "subject.ssn_or_ein", ident.get("tin"))
        _set_if_missing(case, "subject.id_type", ident.get("id_form"))
        _set_if_missing(case, "subject.id_number", ident.get("id_number"))

        accounts = part1.get("accounts") if isinstance(part1.get("accounts"), list) else []
        if accounts and not isinstance(case.get("accounts"), list):
            extracted: List[str] = []
            for item in accounts:
                if not isinstance(item, dict):
                    continue
                acct = str(item.get("account_number") or "").strip()
                if acct:
                    extracted.append(acct)
            if extracted:
                case["accounts"] = extracted

    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    if part2:
        _set_if_missing(case, "amount", _to_float(part2.get("amount_involved")))
        _set_if_missing(case, "total_amount_involved", _to_float(part2.get("amount_involved")))
        _set_if_missing(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", _to_float(part2.get("amount_involved")))
        _set_if_missing(case, "SuspiciousActivityInformation.26_AmountInvolved.no_amount", bool(part2.get("no_amount_involved", False)))

        period = part2.get("activity_period") if isinstance(part2.get("activity_period"), dict) else {}
        from_date = _to_mmddyyyy(period.get("from_date") or period.get("from"))
        to_date = _to_mmddyyyy(period.get("to_date") or period.get("to"))
        _set_if_missing(case, "activity_date_range.start", from_date)
        _set_if_missing(case, "activity_date_range.end", to_date)
        _set_if_missing(case, "SuspiciousActivityInformation.27_DateOrDateRange.from", from_date)
        _set_if_missing(case, "SuspiciousActivityInformation.27_DateOrDateRange.to", to_date)

        category_map = {
            "29_structuring": "29_Structuring",
            "30_terrorist_financing": "30_TerroristFinancing",
            "31_fraud": "31_Fraud",
            "32_casinos": "32_Casinos",
            "33_money_laundering": "33_MoneyLaundering",
            "34_identification": "34_IdentificationDocumentation",
            "35_other_suspicious": "35_OtherSuspiciousActivities",
            "36_insurance": "36_Insurance",
            "37_securities": "37_SecuritiesFuturesOptions",
            "38_mortgage_fraud": "38_MortgageFraud",
        }
        categories = part2.get("suspicious_activity_categories") if isinstance(part2.get("suspicious_activity_categories"), dict) else {}
        for source_key, target_key in category_map.items():
            vals = categories.get(source_key)
            if isinstance(vals, list):
                _set_if_missing(case, f"SuspiciousActivityInformation.{target_key}", vals)

    part3 = case.get("part_3_institution_where_occurred") if isinstance(case.get("part_3_institution_where_occurred"), dict) else {}
    if part3:
        details = part3.get("institution_details") if isinstance(part3.get("institution_details"), dict) else {}
        branch = part3.get("branch_information") if isinstance(part3.get("branch_information"), dict) else {}
        regulator = part3.get("primary_federal_regulator")
        inst_type = part3.get("institution_type")

        _set_if_missing(case, "institution.name", details.get("legal_name"))
        _set_if_missing(case, "institution.tin", details.get("tin"))
        _set_if_missing(case, "institution.ein", details.get("tin"))
        _set_if_missing(case, "institution.primary_federal_regulator", regulator)
        _set_if_missing(case, "institution.type", inst_type)
        _set_if_missing(case, "institution.address", branch.get("branch_address"))
        _set_if_missing(case, "institution.branch_city", branch.get("branch_city"))
        _set_if_missing(case, "institution.branch_state", branch.get("branch_state"))
        _set_if_missing(case, "institution.zip", branch.get("branch_zip"))
        _set_if_missing(case, "institution.country", branch.get("branch_country"))

        _set_if_missing(case, "financial_institution.name", details.get("legal_name"))
        _set_if_missing(case, "financial_institution.branch_address", branch.get("branch_address"))
        _set_if_missing(case, "financial_institution.address", branch.get("branch_address"))
        _set_if_missing(case, "financial_institution.city", branch.get("branch_city"))
        _set_if_missing(case, "financial_institution.state", branch.get("branch_state"))
        _set_if_missing(case, "financial_institution.zip", branch.get("branch_zip"))
        _set_if_missing(case, "financial_institution.country", branch.get("branch_country"))
        _set_if_missing(case, "financial_institution.tin", details.get("tin"))
        _set_if_missing(case, "financial_institution.ein_or_ssn", details.get("tin"))
        _set_if_missing(case, "financial_institution.federal_regulator", regulator)
        _set_if_missing(case, "financial_institution.primary_federal_regulator", regulator)
        _set_if_missing(case, "financial_institution.type", inst_type)

    part4 = case.get("part_4_filing_institution") if isinstance(case.get("part_4_filing_institution"), dict) else {}
    if part4:
        details = part4.get("filer_details") if isinstance(part4.get("filer_details"), dict) else {}
        address = part4.get("address") if isinstance(part4.get("address"), dict) else {}
        regulator = part4.get("primary_federal_regulator")
        inst_type = part4.get("institution_type")

        _set_if_missing(case, "filing_institution.name", details.get("legal_name"))
        _set_if_missing(case, "filing_institution.tin", details.get("tin"))
        _set_if_missing(case, "filing_institution.address", address.get("street"))
        _set_if_missing(case, "filing_institution.city", address.get("city"))
        _set_if_missing(case, "filing_institution.state", address.get("state"))
        _set_if_missing(case, "filing_institution.zip", address.get("zip_code"))
        _set_if_missing(case, "filing_institution.country", address.get("country_code"))
        _set_if_missing(case, "filing_institution.federal_regulator", regulator)
        _set_if_missing(case, "filing_institution.primary_federal_regulator", regulator)
        _set_if_missing(case, "filing_institution.type", inst_type)
        _set_if_missing(case, "filing_institution.contact_office", part4.get("contact_office"))
        _set_if_missing(case, "filing_institution.contact_phone", part4.get("contact_phone"))
        _set_if_missing(case, "filing_institution.date_filed", _to_mmddyyyy(part4.get("filing_date") or report_meta.get("filing_date")))

        if not isinstance(case.get("institution"), dict):
            _set_if_missing(case, "institution.name", details.get("legal_name"))
            _set_if_missing(case, "institution.tin", details.get("tin"))

    if isinstance(case.get("narrative"), dict):
        narrative_text = case.get("narrative", {}).get("text")
        if isinstance(narrative_text, str) and narrative_text.strip():
            case["narrative"] = narrative_text.strip()

    if not isinstance(case.get("alert"), dict):
        red_flags = case.get("red_flags") if isinstance(case.get("red_flags"), list) else []
        if red_flags:
            trigger_reasons: List[str] = []
            labels: List[str] = []
            alerts: List[Dict[str, Any]] = []
            for item in red_flags:
                if not isinstance(item, dict):
                    continue
                indicator = str(item.get("indicator") or "").strip()
                description = str(item.get("description") or "").strip()
                severity = str(item.get("severity") or "medium").strip().lower() or "medium"
                reference = str(item.get("regulatory_reference") or "").strip()
                if indicator:
                    labels.append(indicator)
                if description:
                    trigger_reasons.append(description)
                alerts.append({
                    "subtype": indicator,
                    "description": description,
                    "severity": severity,
                    "rule_id": reference,
                })
            if labels or trigger_reasons:
                case["alert"] = {
                    "subtype": labels[0] if labels else "",
                    "red_flags": labels,
                    "trigger_reasons": trigger_reasons,
                }
            if alerts and not isinstance(case.get("alerts"), list):
                case["alerts"] = alerts

    law_enforcement = (
        case.get("part_4_filing_institution", {}).get("law_enforcement_contact")
        if isinstance(case.get("part_4_filing_institution"), dict)
        else {}
    )
    if isinstance(law_enforcement, dict) and law_enforcement:
        _set_if_missing(case, "external_signals.law_enforcement_contacted.contacted", bool(law_enforcement.get("contacted", False)))
        _set_if_missing(case, "external_signals.law_enforcement_contacted.agency", law_enforcement.get("agency"))
        _set_if_missing(case, "external_signals.law_enforcement_contacted.contact_name", law_enforcement.get("contact_name"))
        _set_if_missing(case, "external_signals.law_enforcement_contacted.phone", law_enforcement.get("contact_phone"))
        _set_if_missing(case, "external_signals.law_enforcement_contacted.date", _to_mmddyyyy(law_enforcement.get("contact_date")))

    _canonicalize_transactions(case)
    return case


def normalize_case_data(case_data: Any) -> Dict[str, Any]:
    """
    Normalize supported case payloads into a single dict.

    Accepts either:
    - a case dict
    - a list containing one or more case dicts (uses first dict item)
    """
    normalized = _normalize_obj(case_data)
    if isinstance(normalized, dict):
        return _canonicalize_extended_case(normalized)
    if isinstance(normalized, list):
        for item in normalized:
            if isinstance(item, dict):
                return _canonicalize_extended_case(item)
    return {}


class SARFieldMapper:
    """Map case JSON data to SAR PDF AcroForm field IDs."""

    REGULATOR_MAP = {
        "FRB": "/0",
        "Federal Reserve": "/0",
        "FEDERAL RESERVE": "/0",
        "FDIC": "/1",
        "NCUA": "/2",
        "OCC": "/3",
        "OTS": "/4",
    }

    LE_AGENCY_MAP = {
        "DEA": "item40a",
        "FBI": "item40b",
        "IRS": "item40c",
        "Postal Inspection": "item40d",
        "Secret Service": "item40e",
        "U.S. Customs": "item40f",
        "Other Federal": "item40g",
        "State": "item40h",
        "Local": "item40i",
    }

    def __init__(self, case_data: Dict):
        self.case = normalize_case_data(case_data)
        inst = self.case.get("institution", {})
        subject = self.case.get("subject", {})
        activity = self.case.get("SuspiciousActivityInformation", {})
        signals = self.case.get("external_signals", {})
        self.inst = inst if isinstance(inst, dict) else {}
        self.subject = subject if isinstance(subject, dict) else {}
        self.activity = activity if isinstance(activity, dict) else {}
        txns = self.case.get("transactions", [])
        self.txns = [tx for tx in txns if isinstance(tx, dict)] if isinstance(txns, list) else []
        self.signals = signals if isinstance(signals, dict) else {}
        le = self.signals.get("law_enforcement_contacted", {})
        self.le = le if isinstance(le, dict) else {}

    def map_all_fields(self, template_variant: str = "legacy") -> Dict[str, str]:
        fields: Dict[str, str] = {}
        variant = (template_variant or "legacy").strip().lower()
        if variant == "fincen_acroform":
            fields.update(self._map_fincen_acroform())
        elif variant == "all":
            fields.update(self._map_institution())
            fields.update(self._map_suspect())
            fields.update(self._map_suspicious_activity())
            fields.update(self._map_law_enforcement())
            fields.update(self._map_contact())
            fields.update(self._map_narrative())
            fields.update(self._map_fincen_acroform())
        else:
            fields.update(self._map_institution())
            fields.update(self._map_suspect())
            fields.update(self._map_suspicious_activity())
            fields.update(self._map_law_enforcement())
            fields.update(self._map_contact())
            fields.update(self._map_narrative())
        return {
            key: str(value)
            for key, value in fields.items()
            if value is not None and value != ""
        }

    def _map_institution(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        f["item2"] = self.inst.get("name", "")
        fallback_city, fallback_state = self._city_state_from_txns()
        city = (self.inst.get("branch_city", "") or "").strip() or fallback_city
        state = ((self.inst.get("branch_state", "") or "").strip()[:2] or fallback_state)
        zip_code = (
            (self.inst.get("zip", "") or "").strip()
            or (self.inst.get("postal_code", "") or "").strip()
        )

        f["item6"] = city
        f["item7"] = state
        f["item9"] = self._compose_address_line(city, state, zip_code)
        f["item10"] = city
        f["item11-1"] = state

        regulator = (self.inst.get("primary_federal_regulator", "") or "").strip()
        if regulator in self.REGULATOR_MAP:
            f["item5"] = self.REGULATOR_MAP[regulator]
        elif regulator.upper() in self.REGULATOR_MAP:
            f["item5"] = self.REGULATOR_MAP[regulator.upper()]

        accounts = self._collect_accounts()
        acct_fields = ["item14a", "item14b", "item14c", "item14d"]
        acct_open_fields = ["item14a-1", "item14b-1", "item14c-1", "item14d-1"]
        for idx, acct in enumerate(accounts[:4]):
            f[acct_fields[idx]] = acct
            f[acct_open_fields[idx]] = "/Yes"

        return f

    def _collect_accounts(self) -> List[str]:
        seen = set()
        out: List[str] = []
        for tx in self.txns:
            for key in ("origin_account", "destination_account"):
                acct = tx.get(key, "")
                if acct and acct not in seen:
                    seen.add(acct)
                    out.append(acct)
        return out

    def _map_suspect(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        full_name = self.subject.get("name", "") or ""
        subject_type = (self.subject.get("type", "") or "").lower()
        treat_as_entity = subject_type != "individual" or self._looks_like_entity_name(full_name)

        if not treat_as_entity:
            parsed = self._split_individual_name(full_name)
            f["item15"] = parsed["last"]
            f["item16"] = parsed["first"]
            f["item17"] = parsed["middle"]
        else:
            f["item15"] = full_name

        f["item23"] = self.subject.get("country", "US")
        f["item26"] = self.subject.get("industry_or_occupation", "")

        # Conservative defaults for missing explicit data.
        f["item28"] = "/1"
        f["item30"] = "/6"
        f["item31"] = "/1"
        return f

    @staticmethod
    def _split_individual_name(name: str) -> Dict[str, str]:
        clean = " ".join((name or "").split())
        if not clean:
            return {"first": "", "middle": "", "last": ""}
        parts = clean.split(" ")
        if len(parts) == 1:
            return {"first": parts[0], "middle": "", "last": parts[0]}
        if len(parts) == 2:
            return {"first": parts[0], "middle": "", "last": parts[1]}
        return {"first": parts[0], "middle": " ".join(parts[1:-1]), "last": parts[-1]}

    def _map_suspicious_activity(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        date_range = self.activity.get("27_DateOrDateRange", {})
        self._apply_mmddyyyy(date_range.get("from", ""), ("item33-1", "item33-2", "item33-3"), f)
        self._apply_mmddyyyy(date_range.get("to", ""), ("item33-4", "item33-5", "item33-6"), f)

        amount_val = self._resolve_amount_involved()
        amount_digits = f"{int(round(amount_val)):011d}"
        # Some SAR templates expose item34 as one text field, while others split
        # it across 11 digit boxes (item34-1..item34-11). Populate both styles.
        f["item34"] = str(int(round(amount_val)))
        for idx, field_id in enumerate(
            [
                "item34-1",
                "item34-2",
                "item34-3",
                "item34-4",
                "item34-5",
                "item34-6",
                "item34-7",
                "item34-8",
                "item34-9",
                "item34-10",
                "item34-11",
            ]
        ):
            f[field_id] = amount_digits[idx]

        summary_fields, summary_notes = self._map_summary_characterization_fields()
        f.update(summary_fields)
        if summary_notes:
            f["item35s"] = "/Yes"
            f["item35s-1"] = "; ".join(summary_notes)[:100]

        f["item38"] = "/1"
        f["item39"] = "/1"
        return f

    @staticmethod
    def _has_items(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return len(SARFieldMapper._to_list(value)) > 0

    @staticmethod
    def _to_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                normalized = _normalize_obj(item)
                if normalized in (None, False, ""):
                    continue
                if isinstance(normalized, list):
                    out.extend(SARFieldMapper._to_list(normalized))
                else:
                    out.append(str(normalized))
            return out
        normalized = _normalize_obj(value)
        if normalized in (None, False, ""):
            return []
        return [str(normalized)]

    @staticmethod
    def _contains_case_insensitive(value, needle: str) -> bool:
        for item in SARFieldMapper._to_list(value):
            if needle.lower() in item.lower():
                return True
        return False

    @staticmethod
    def _apply_mmddyyyy(value: str, field_ids, out: Dict[str, str]) -> None:
        text = (value or "").strip()
        if not text:
            return
        parts = text.split("/")
        if len(parts) != 3:
            return
        out[field_ids[0]] = parts[0]
        out[field_ids[1]] = parts[1]
        out[field_ids[2]] = parts[2]

    @staticmethod
    def _as_bool(value: Any) -> bool:
        normalized = _normalize_scalar(value)
        if isinstance(normalized, bool):
            return normalized
        if isinstance(normalized, (int, float)):
            return normalized != 0
        return bool(normalized)

    def _map_law_enforcement(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        if not self._as_bool(self.le.get("contacted", False)):
            return f

        agency = self.le.get("agency") or ""
        contact_name = self.le.get("contact_name") or ""
        phone = self.le.get("phone", "") or ""

        matched = self.LE_AGENCY_MAP.get(agency)
        if matched:
            f[matched] = "/Yes"
        elif agency:
            f["item40j"] = "/Yes"
            f["item40j-1"] = agency[:80]

        if contact_name:
            f["item41"] = contact_name

        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            f["item42-1"] = digits[:3]
            f["item42-2"] = digits[3:10]

        return f

    def _map_contact(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        contact_name = self.inst.get("contact_officer", "") or ""
        if contact_name:
            parts = contact_name.split(" ", 1)
            f["item46"] = parts[0]
            f["item45"] = parts[1] if len(parts) > 1 else parts[0]
        f["item48"] = "Compliance Officer"

        phone = self.inst.get("contact_phone", "") or ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            f["item49-1"] = digits[:3]
            f["item49-2"] = digits[3:10]

        now = datetime.now()
        f["item50-1"] = now.strftime("%m")
        f["item50-2"] = now.strftime("%d")
        f["item50-3"] = now.strftime("%Y")
        return f

    def _map_narrative(self) -> Dict[str, str]:
        provided_narrative = _normalize_scalar(self.case.get("narrative"))
        if isinstance(provided_narrative, str) and provided_narrative:
            return {"item51": provided_narrative[:4000]}

        blocks: List[str] = []
        subject_name = self.subject.get("name", "Unknown")
        occ = self.subject.get("industry_or_occupation", "")
        country = self.subject.get("country", "")

        line = f"SUBJECT: {subject_name}"
        if occ:
            line += f", {occ}"
        if country:
            line += f" ({country})"
        blocks.append(line)

        prior = self.subject.get("prior_sars", [])
        if prior:
            blocks.append(f"PRIOR SARS: {', '.join(prior)}")

        date_range = self.activity.get("27_DateOrDateRange", {})
        if date_range.get("from") and date_range.get("to"):
            blocks.append(
                f"ACTIVITY PERIOD: {date_range['from']} through {date_range['to']}"
            )

        alert = self.case.get("alert", {})
        flags = self._to_list(alert.get("red_flags")) + self._to_list(
            alert.get("trigger_reasons")
        )
        if flags:
            blocks.append(f"RED FLAGS DETECTED: {'; '.join(flags)}")

        amount = self._resolve_amount_involved()
        if amount:
            blocks.append(f"TOTAL AMOUNT INVOLVED: ${float(amount):,.2f}")

        if self.txns:
            blocks.append(f"TRANSACTIONS ({len(self.txns)} total):")
            for tx in self.txns:
                ts = tx.get("timestamp", "")
                usd = float(tx.get("amount_usd", 0) or 0)
                orig = tx.get("origin_account", "")
                dest = tx.get("destination_account", "")
                loc = tx.get("location", "")
                note = tx.get("notes", "")
                tx_line = f"  {ts}: ${usd:,.2f} from {orig} to {dest}"
                if loc:
                    tx_line += f" | {loc}"
                if note:
                    tx_line += f" | {note}"
                blocks.append(tx_line)

        if self._has_items(self.activity.get("29_Structuring")):
            blocks.append(
                "STRUCTURING: " + "; ".join(self._to_list(self.activity["29_Structuring"]))
            )
        if self._has_items(self.activity.get("33_MoneyLaundering")):
            blocks.append(
                "MONEY LAUNDERING INDICATORS: "
                + "; ".join(self._to_list(self.activity["33_MoneyLaundering"]))
            )
        if self._has_items(self.activity.get("31_Fraud")):
            blocks.append("FRAUD: " + "; ".join(self._to_list(self.activity["31_Fraud"])))
        if self._has_items(self.activity.get("35_OtherSuspiciousActivities")):
            blocks.append(
                "OTHER SUSPICIOUS ACTIVITY: "
                + "; ".join(self._to_list(self.activity["35_OtherSuspiciousActivities"]))
            )

        dq_notes = self.case.get("data_quality", {}).get("notes", "")
        if dq_notes:
            blocks.append(f"NOTES: {dq_notes}")

        media = self._to_list(self.signals.get("adverse_media"))
        if media:
            blocks.append("ADVERSE MEDIA: " + "; ".join(media))

        narrative = "\n\n".join(blocks)
        return {"item51": narrative[:4000]}

    def _resolve_amount_involved(self) -> float:
        """
        Resolve SAR amount with fallbacks used by different pipeline stages.
        Priority:
        1) SuspiciousActivityInformation.26_AmountInvolved.amount_usd
        2) top-level total_amount_involved
        3) sum(transactions[].amount_usd|amount)
        """
        amount_info = self.activity.get("26_AmountInvolved", {})
        if isinstance(amount_info, dict):
            amount = self._to_float(amount_info.get("amount_usd"))
            if amount > 0:
                return amount

        amount = self._to_float(self.case.get("total_amount_involved"))
        if amount > 0:
            return amount

        tx_total = 0.0
        for tx in self.txns:
            if not isinstance(tx, dict):
                continue
            tx_total += self._to_float(tx.get("amount_usd"))
            if self._to_float(tx.get("amount_usd")) == 0:
                tx_total += self._to_float(tx.get("amount"))
        return tx_total

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            if isinstance(value, str):
                cleaned = value.replace(",", "").replace("$", "").strip()
                return float(cleaned or 0)
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _map_summary_characterization_fields(self) -> tuple[Dict[str, str], List[str]]:
        """
        Map SAR item 35 summary characterization checkboxes conservatively.
        Returns checkbox fields plus free-text notes for item35s-1.
        """
        fields: Dict[str, str] = {}
        notes: List[str] = []

        structuring = self._to_list(self.activity.get("29_Structuring"))
        fraud = self._to_list(self.activity.get("31_Fraud"))
        money_laundering = self._to_list(self.activity.get("33_MoneyLaundering"))
        other = self._to_list(self.activity.get("35_OtherSuspiciousActivities"))
        mortgage = self._to_list(self.activity.get("38_MortgageFraud"))
        terrorist = self._to_list(self.activity.get("30_TerroristFinancing"))

        if structuring or money_laundering:
            fields["item35a"] = "/Yes"
        if mortgage:
            fields["item35t"] = "/Yes"

        fraud_text = " | ".join(fraud).lower()
        keyword_map = {
            "item35c": ("check fraud", "check"),
            "item35d": ("kiting",),
            "item35e": ("commercial loan",),
            "item35f": ("computer intrusion", "cyber", "malware", "phishing"),
            "item35g": ("consumer loan",),
            "item35h": ("counterfeit check",),
            "item35i": ("counterfeit credit", "counterfeit debit"),
            "item35j": ("counterfeit instrument",),
            "item35k": ("credit card fraud", "credit card"),
            "item35l": ("debit card fraud", "debit card"),
            "item35m": ("embezzlement", "defalcation"),
            "item35n": ("false statement",),
            "item35o": ("financial institution fraud", "institution fraud"),
            "item35p": ("identity theft", "account takeover"),
            "item35r": ("mail fraud",),
            "item35u": ("mysterious disappearance",),
        }
        for field_id, needles in keyword_map.items():
            if any(needle in fraud_text for needle in needles):
                fields[field_id] = "/Yes"

        # No dedicated legacy checkbox for wire fraud; keep it in free-text.
        if "wire fraud" in fraud_text:
            notes.append("Wire fraud")

        notes.extend(other)
        notes.extend(terrorist)

        if not fields and (structuring or fraud or money_laundering or other or mortgage or terrorist):
            fields["item35s"] = "/Yes"

        return fields, notes

    def _map_fincen_acroform(self) -> Dict[str, str]:
        """
        Map key values to the newer FinCEN SAR AcroForm field names.

        This map prioritizes reliable population of required identity/institution
        fields and fills repeated template sections with the same normalized values.
        """
        f: Dict[str, str] = {}

        subject_type = (self.subject.get("type", "") or "").lower()
        subject_name = self.subject.get("name", "") or ""
        treat_as_entity = subject_type != "individual" or self._looks_like_entity_name(subject_name)
        if not treat_as_entity:
            parsed = self._split_individual_name(subject_name)
            f["3  Individuals last name or entitys legal name a Unk"] = parsed["last"]
            f["4  First name a Unk"] = parsed["first"]
            f["5  Middle initial"] = parsed["middle"][:1]
        else:
            f["3  Individuals last name or entitys legal name a Unk"] = subject_name

        fallback_city, fallback_state = self._city_state_from_txns()
        subject_city = (self.subject.get("city", "") or "").strip() or fallback_city or "UNKNOWN"
        subject_state = (self.subject.get("state", "") or "").strip()[:2] or fallback_state or "NA"
        subject_zip = (
            (self.subject.get("zip", "") or "").strip()
            or (self.subject.get("postal_code", "") or "").strip()
            or "00000"
        )
        subject_address = (self.subject.get("address", "") or "").strip()
        if not subject_address:
            subject_address = self._compose_address_line(subject_city, subject_state, subject_zip)

        f["7  Occupation or type of business"] = self.subject.get("industry_or_occupation", "")
        f["8 Address a Unk"] = subject_address
        f["9  City a Unk"] = subject_city
        f["11  ZIPPostal Code a Unk"] = subject_zip
        country = (self.subject.get("country", "US") or "US").strip().upper()[:2]
        f["12  Country code"] = country

        subject_tin = (
            self.subject.get("tin")
            or self.subject.get("ssn_or_ein")
            or self.subject.get("ssn")
            or self.subject.get("ein")
            or "UNKNOWN"
        )
        f["13  TIN a Unk"] = subject_tin

        # Populate generic date fields that represent date-of-birth/date blocks
        # in this template family.
        dob_raw = (
            self.subject.get("date_of_birth")
            or self.subject.get("dob")
            or self.subject.get("onboarding_date")
            or self.case.get("generated_at")
            or ""
        )
        mm = dd = yyyy = ""
        dob_text = str(dob_raw or "").strip()
        if dob_text:
            parsed_dt = None
            for fmt in (
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
            ):
                try:
                    parsed_dt = datetime.strptime(dob_text, fmt)
                    break
                except ValueError:
                    continue
            if parsed_dt is not None:
                mm = parsed_dt.strftime("%m")
                dd = parsed_dt.strftime("%d")
                yyyy = parsed_dt.strftime("%Y")
            else:
                m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", dob_text)
                if m:
                    mm, dd, yyyy = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)

        if mm:
            f["MM"] = mm
        if dd:
            f["DD"] = dd
        if yyyy:
            f["YYYY"] = yyyy

        alert_email = (
            self.case.get("institution", {}).get("contact_email")
            if isinstance(self.case.get("institution"), dict)
            else ""
        ) or ""
        f["19 Email adress (If available)"] = alert_email

        fin_inst = (
            self.case.get("financial_institution")
            if isinstance(self.case.get("financial_institution"), dict)
            else {}
        )
        inst_city = (self.inst.get("branch_city", "") or "").strip() or fallback_city or "UNKNOWN"
        inst_state = (self.inst.get("branch_state", "") or "").strip()[:2] or fallback_state or "NA"
        inst_zip = (
            (self.inst.get("zip", "") or "").strip()
            or (self.inst.get("postal_code", "") or "").strip()
            or (fin_inst.get("zip", "") or "").strip()
            or "00000"
        )
        inst_address = (self.inst.get("address", "") or "").strip()
        if not inst_address:
            inst_address = self._compose_address_line(inst_city, inst_state, inst_zip)

        inst_tin = (
            self.inst.get("ein")
            or self.inst.get("tin")
            or self.inst.get("ein_or_ssn")
            or fin_inst.get("ein_or_ssn")
            or fin_inst.get("tin")
            or "UNKNOWN"
        )

        f["21a  Institution  TIN"] = inst_tin
        f["53  Legal name of financial institution a  Unk"] = self.inst.get("name", "")
        f["55  TIN a  Unk"] = inst_tin
        f["57  Address a  Unk"] = inst_address
        f["58  City a  Unk"] = inst_city
        f["59 State"] = inst_state
        f["60  ZIPPostal Code"] = inst_zip

        # Fill repeated institution/branch sections that are present on this template.
        f["62  Internal controlfile number"] = str(self.case.get("case_id", ""))
        f["65  Address of branch or office where activity occurred If no branch activity involved check this box a"] = inst_address
        f["67  City"] = inst_city
        f["68  State"] = inst_state
        f["69  ZIPPostal Code"] = inst_zip
        f["70  Country 2letter code"] = country
        f["72 Adress"] = inst_address
        f["74 City"] = inst_city
        f["75 State"] = inst_state
        f["76  ZIPPostal Code"] = inst_zip
        f["77  Country 2letter code"] = country
        f["80  TIN"] = inst_tin
        f["85  AddressRow1"] = inst_address
        f["85  Address"] = inst_address
        f["86  City"] = inst_city
        f["87 State"] = inst_state
        f["88 Zip postal code"] = inst_zip
        f["91  Internal controlfile number"] = str(self.case.get("case_id", ""))

        # TIN type radio hints.
        if treat_as_entity or re.search(r"\d{2}-?\d{7}", str(subject_tin)):
            f["EIN"] = "/Yes"
        elif subject_tin and str(subject_tin).strip().upper() != "UNKNOWN":
            f["SSNITIN"] = "/Yes"

        if re.search(r"\d{2}-?\d{7}", str(inst_tin)):
            f["EIN_2"] = "/Yes"
        elif inst_tin and str(inst_tin).strip().upper() != "UNKNOWN":
            f["SSNITIN_2"] = "/Yes"

        contact_name = self.inst.get("contact_officer", "") or ""
        f["79 Filer name"] = contact_name
        f[" 96 Filing institution contact office"] = "Compliance Office"
        f["97  Filing institution contact office phone number Include Area Code"] = (
            self._normalize_phone(self.inst.get("contact_phone", ""))
        )

        if self._as_bool(self.le.get("contacted", False)):
            f["92  LE contact agency"] = self.le.get("agency", "") or ""
            f["93  LE contact name"] = self.le.get("contact_name", "") or ""
            f["94  LE contact phone number Include Area Code"] = self._normalize_phone(
                self.le.get("phone", "")
            )

        # Ensure key suspicious amount appears in a visible free-text product field
        # for templates where dedicated amount boxes are non-standardly named.
        amount_val = self._resolve_amount_involved()
        amount_info = self.activity.get("26_AmountInvolved", {})
        no_amount = self._as_bool(amount_info.get("no_amount")) if isinstance(amount_info, dict) else False

        if amount_val > 0 and not no_amount:
            f.update(self._map_fincen_item26_amount_fields(amount_val))
        elif no_amount:
            # Item 26.b - no amount involved.
            f["b_10"] = "/Yes"
        else:
            # Item 26.a - amount unknown.
            f["a_7"] = "/Yes"

        # Item 29 - Structuring checks.
        f.update(self._map_fincen_item29_structuring_fields())

        # Item 30-38 summary characterization checks.
        f.update(self._map_fincen_summary_characterization_fields())

        narrative = self._map_narrative().get("item51", "")
        if narrative:
            f["Narrative"] = narrative

        return f

    def _map_fincen_item26_amount_fields(self, amount_val: float) -> Dict[str, str]:
        """
        Best-effort mapping for Item 26 amount boxes on FinCEN AcroForm.

        The current template uses non-semantic field ids (Text2/Text7/Text8/Text9/Text10)
        for dollar boxes plus Text13 for cents.
        """
        fields: Dict[str, str] = {}
        safe_amount = max(float(amount_val or 0.0), 0.0)
        whole = int(safe_amount)
        cents = int(round((safe_amount - whole) * 100))

        # Right-align in a fixed 11-digit whole-dollar layout.
        amount_digits = f"{whole:011d}"

        # Preserve a human-readable backup in a visible text field.
        fields["42 ProductInstrument description If needed"] = f"Total amount involved: ${safe_amount:,.2f}"

        # Populate non-semantic dollar boxes used by item 26 in this template family.
        fields["Text2"] = amount_digits[0:2]
        fields["Text7"] = amount_digits[2:3]
        fields["Text10"] = amount_digits[3:4]
        fields["Text8"] = amount_digits[4:5]
        fields["Text9"] = amount_digits[5:11]
        fields["Text13"] = f"{cents:02d}"
        return fields

    def _map_fincen_item29_structuring_fields(self) -> Dict[str, str]:
        """Map Item 29 (Structuring) checkboxes for the FinCEN AcroForm."""
        fields: Dict[str, str] = {}
        structuring_items = self._to_list(self.activity.get("29_Structuring"))
        if not structuring_items:
            return fields

        combined = " | ".join(structuring_items).lower()
        matched = False

        keyword_map = {
            "a_9": ("recordkeeping", "bsa recordkeeping"),
            "b_11": ("avoid ctr", "ctr requirement"),
            "c_8": ("cancel",),
            "d_6": ("below bsa", "recordkeeping threshold"),
            "e_2": ("below ctr", "ctr threshold", "structured to avoid ctr"),
            "f_2": ("inquiry",),
        }
        for field_id, keywords in keyword_map.items():
            if any(keyword in combined for keyword in keywords):
                fields[field_id] = "/Yes"
                matched = True

        if not matched:
            # Most SARs with item 29 indicate sub-threshold CTR behavior.
            fields["e_2"] = "/Yes"

        other_notes = [
            item
            for item in structuring_items
            if not any(token in item.lower() for token in ("recordkeeping", "ctr", "cancel", "inquiry"))
        ]
        if other_notes:
            fields["z_4"] = "/Yes"
            fields["Other_4"] = "; ".join(other_notes)[:180]

        return fields

    def _map_fincen_summary_characterization_fields(self) -> Dict[str, str]:
        """
        Map Item 30-38 summary characterization categories on the FinCEN AcroForm.

        FinCEN AcroForm field IDs are generic, so this uses first-checkbox-per-section
        mapping to ensure category coverage without over-checking fine-grained options.
        """
        fields: Dict[str, str] = {}

        def mark_if_present(path: str, checkbox: str, other_checkbox: str | None = None, other_text_field: str | None = None) -> None:
            values = self._to_list(self.activity.get(path))
            if not values:
                return
            fields[checkbox] = "/Yes"
            if other_checkbox:
                # Keep explicit "other" checkbox available when text-heavy lists are supplied.
                if len(values) > 1:
                    fields[other_checkbox] = "/Yes"
            if other_text_field:
                joined = "; ".join(str(v) for v in values if str(v).strip())
                if joined:
                    fields[other_text_field] = joined[:180]

        # 30 Terrorist financing
        mark_if_present("30_TerroristFinancing", "a_10", other_checkbox="z_5", other_text_field="Other_4")
        # 31 Fraud
        mark_if_present("31_Fraud", "a_11", other_checkbox="z_6")
        # 32 Casinos
        mark_if_present("32_Casinos", "a_12", other_checkbox="z_7")
        # 33 Money laundering
        mark_if_present("33_MoneyLaundering", "a_13", other_checkbox="z_8")
        # 34 Identification/documentation issues
        mark_if_present("34_IdentificationDocumentation", "a_14", other_checkbox="z_9")
        # 35 Other suspicious activities
        mark_if_present("35_OtherSuspiciousActivities", "a_15", other_checkbox="z_10", other_text_field="Unclear or no insurable interest")
        # 36 Insurance
        mark_if_present("36_Insurance", "a_16", other_checkbox="z_11", other_text_field="Unauthorized pooling")
        # 37 Securities/futures/options
        mark_if_present("37_SecuritiesFuturesOptions", "a_17", other_checkbox="z_12", other_text_field="Unlicensed or unregistered MSB")
        # 38 Mortgage fraud
        mark_if_present("38_MortgageFraud", "c_16", other_checkbox="d_14", other_text_field="Foreclosure fraud")

        return fields

    def _city_state_from_txns(self) -> tuple[str, str]:
        for tx in self.txns:
            location = tx.get("location", "") or ""
            if "," in location:
                city, state = location.split(",", 1)
                return city.strip(), state.strip()[:2]
        return "", ""

    @staticmethod
    def _compose_address_line(city: str, state: str, zip_code: str) -> str:
        city = (city or "").strip()
        state = (state or "").strip()
        zip_code = (zip_code or "").strip()
        parts = []
        if city or state:
            parts.append(", ".join(part for part in [city, state] if part))
        if zip_code:
            if parts:
                parts[0] = f"{parts[0]} {zip_code}".strip()
            else:
                parts.append(zip_code)
        return parts[0] if parts else ""

    @staticmethod
    def _looks_like_entity_name(name: str) -> bool:
        upper = f" {(name or '').upper()} "
        entity_tokens = (" LLC ", " INC ", " CORP ", " CORPORATION ", " LTD ", " COMPANY ", " CO. ")
        return any(token in upper for token in entity_tokens)

    @staticmethod
    def _normalize_phone(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            return digits[:10]
        if digits:
            return digits.ljust(10, "0")
        return ""


class CTRFieldMapper:
    """Map case JSON data to CTR PDF AcroForm field IDs."""

    # The CTR template in this repo has generic field IDs. These defaults are
    # intentionally simple and can be overridden by providing case_data["ctr_field_map"].
    DEFAULT_FIELD_MAP = {
        "institution_name": "item-1",
        "institution_ein": "item-2",
        "institution_address": "item-3",
        "institution_city_state": "item-4",
        "conductor_name": "item-5",
        "conductor_country": "item-6",
        "total_cash_amount": "item-7",
        "prepared_date": "item-8",
        "contact_name": "text1",
        "contact_phone": "text2",
        "account_numbers": "text3",
        "transaction_summary": "text4",
    }

    def __init__(self, case_data: Dict):
        self.case = normalize_case_data(case_data)
        inst = self.case.get("institution", {})
        subject = self.case.get("subject", {})
        activity = self.case.get("SuspiciousActivityInformation", {})
        self.inst = inst if isinstance(inst, dict) else {}
        self.subject = subject if isinstance(subject, dict) else {}
        self.activity = activity if isinstance(activity, dict) else {}
        txns = self.case.get("transactions", [])
        self.txns = [tx for tx in txns if isinstance(tx, dict)] if isinstance(txns, list) else []

        overrides = self.case.get("ctr_field_map", {})
        if isinstance(overrides, dict):
            self.field_map = {**self.DEFAULT_FIELD_MAP, **overrides}
        else:
            self.field_map = dict(self.DEFAULT_FIELD_MAP)

    def map_all_fields(self) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        fields.update(self._map_institution())
        fields.update(self._map_conductor())
        fields.update(self._map_transactions())
        fields.update(self._map_contact())
        return {
            key: str(value)
            for key, value in fields.items()
            if key and value is not None and value != ""
        }

    def _put(self, out: Dict[str, str], logical_key: str, value: str) -> None:
        field_id = self.field_map.get(logical_key)
        if field_id and value not in ("", None):
            out[field_id] = str(value)

    def _map_institution(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        name = self.inst.get("name", "")
        ein = self.inst.get("ein", "")
        addr = self.inst.get("address", "")
        city = self.inst.get("branch_city", "")
        state = (self.inst.get("branch_state", "") or "")[:2]
        city_state = f"{city}, {state}".strip(", ")

        self._put(f, "institution_name", name)
        self._put(f, "institution_ein", ein)
        self._put(f, "institution_address", addr)
        self._put(f, "institution_city_state", city_state)
        return f

    def _map_conductor(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        full_name = self.subject.get("name", "") or "Unknown"
        country = self.subject.get("country", "US")
        self._put(f, "conductor_name", full_name)
        self._put(f, "conductor_country", country)
        return f

    def _map_transactions(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        total_cash = calculate_total_cash_amount(self.case)
        if total_cash:
            self._put(f, "total_cash_amount", f"{total_cash:,.2f}")

        accounts = []
        seen = set()
        for tx in self.txns:
            for key in ("origin_account", "destination_account"):
                value = tx.get(key, "")
                if value and value not in seen:
                    seen.add(value)
                    accounts.append(value)
        if accounts:
            self._put(f, "account_numbers", ", ".join(accounts[:4]))

        tx_lines: List[str] = []
        for tx in self.txns[:6]:
            ts = tx.get("timestamp", "")
            amt = float(tx.get("amount_usd", 0) or 0)
            instrument = tx.get("instrument_type", "")
            product = tx.get("product_type", "")
            tx_lines.append(f"{ts} ${amt:,.2f} {instrument} {product}".strip())
        if tx_lines:
            self._put(f, "transaction_summary", " | ".join(tx_lines)[:500])

        return f

    def _map_contact(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        contact_name = self.inst.get("contact_officer", "")
        if contact_name:
            self._put(f, "contact_name", contact_name)

        phone = self.inst.get("contact_phone", "") or ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            self._put(f, "contact_phone", f"{digits[:3]}-{digits[3:10]}")

        now = datetime.now().strftime("%m/%d/%Y")
        self._put(f, "prepared_date", now)
        return f


def _to_list(value) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_to_list(item))
        return out
    normalized = _normalize_obj(value)
    if normalized in (None, False, ""):
        return []
    return [str(normalized)]


def _is_cash_transaction(tx: Dict) -> bool:
    instrument = (tx.get("instrument_type", "") or "").strip().lower()
    product = (tx.get("product_type", "") or "").strip().lower()
    tx_type = (tx.get("type", "") or "").strip().lower()
    text = " ".join([instrument, product, tx_type])
    return any(token in text for token in ("cash", "currency", "cash_deposit"))


def calculate_total_cash_amount(case_data: Any) -> float:
    """Return total cash amount from case transactions."""
    normalized_case = normalize_case_data(case_data)
    total = 0.0
    for tx in normalized_case.get("transactions", []):
        if not isinstance(tx, dict):
            continue
        if _is_cash_transaction(tx):
            total += float(tx.get("amount_usd", 0) or 0)
    return total


def has_suspicious_activity(case_data: Any) -> bool:
    """Detect SAR-triggering indicators from structured case fields."""
    normalized_case = normalize_case_data(case_data)
    activity = normalized_case.get("SuspiciousActivityInformation", {})
    suspicious_fields = (
        "29_Structuring",
        "30_TerroristFinancing",
        "31_Fraud",
        "33_MoneyLaundering",
        "35_OtherSuspiciousActivities",
        "38_MortgageFraud",
    )
    if any(_to_list(activity.get(name)) for name in suspicious_fields):
        return True

    alert = normalized_case.get("alert", {})
    if any(_text_indicates_suspicion(x) for x in _to_list(alert.get("red_flags"))):
        return True
    if any(_text_indicates_suspicion(x) for x in _to_list(alert.get("trigger_reasons"))):
        return True
    return False


def _text_indicates_suspicion(text: str) -> bool:
    lowered = str(_normalize_scalar(text) or "").lower()
    if not lowered:
        return False
    suspicious_tokens = (
        "structur",
        "fraud",
        "launder",
        "suspicious",
        "terror",
        "sanction",
        "unusual",
        "no apparent",
        "kiting",
        "embezz",
    )
    if any(token in lowered for token in suspicious_tokens):
        return True
    # Threshold-only phrases should not force SAR by themselves.
    non_sar_tokens = (
        "exceeds $10,000",
        "exceeds 10,000",
        "10k",
        "threshold",
    )
    if any(token in lowered for token in non_sar_tokens):
        return False
    return False


def determine_report_types(case_data: Any) -> List[str]:
    """
    Determine which report(s) to file.

    Rules:
    - total cash >= 10,000 => CTR
    - suspicious activity indicators => SAR
    - both can be required
    """
    report_types: List[str] = []
    if calculate_total_cash_amount(case_data) >= 10000.0:
        report_types.append("CTR")
    if has_suspicious_activity(case_data):
        report_types.append("SAR")
    return report_types
