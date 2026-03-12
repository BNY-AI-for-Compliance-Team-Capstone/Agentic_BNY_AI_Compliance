"""
Narrative quality validation for generated SAR narratives.

Checks that the narrative passes structural, tone, and factual-alignment criteria
as defined in docs/NARRATIVE_VALIDATION.md.
"""

import re
from dataclasses import dataclass, field
from typing import Any

# Minimum and maximum word count for a typical SAR narrative
MIN_WORDS = 50
MAX_WORDS = 1200

# Forbidden phrases that indicate non-compliant tone (legal conclusions, accusatory language)
FORBIDDEN_PHRASES = [
    r"\bguilty\b",
    r"\bcommitted\b",
    r"\bdefinitely\b",
    r"\bcertainly\s+(committed|engaged|laundered)\b",
    r"\bconvicted\b",
    r"\bproven\s+(to\s+be|that)\b",
    r"\bwithout\s+a\s+doubt\b",
    r"\bclearly\s+(committed|guilty)\b",
]


@dataclass
class ValidationCheck:
    """Single validation check result."""
    name: str
    passed: bool
    message: str = ""


@dataclass
class NarrativeValidationResult:
    """Result of validating a generated narrative against input and criteria."""
    passed: bool
    checks: list[ValidationCheck] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str = "") -> None:
        self.checks.append(ValidationCheck(name=name, passed=passed, message=message))
        if not passed:
            self.passed = False

    def failed_checks(self) -> list[ValidationCheck]:
        return [c for c in self.checks if not c.passed]


def _extract_text_for_grounding(data: dict[str, Any]) -> set[str]:
    """Extract strings from input that should appear or be reflected in the narrative (case-insensitive)."""
    out: set[str] = set()
    if not data:
        return out

    def collect(obj: Any) -> None:
        if isinstance(obj, str):
            s = obj.strip()
            if s and len(s) > 1:
                out.add(s.lower())
        elif isinstance(obj, dict):
            for v in obj.values():
                collect(v)
        elif isinstance(obj, list):
            for v in obj:
                collect(v)
        elif isinstance(obj, (int, float)):
            out.add(str(obj))

    collect(data)
    return out


def validate_narrative(
    narrative: str,
    input_data: dict[str, Any],
    *,
    min_words: int = MIN_WORDS,
    max_words: int = MAX_WORDS,
    strict_grounding: bool = False,
) -> NarrativeValidationResult:
    """
    Validate a generated SAR narrative against structure, tone, and input alignment.

    Args:
        narrative: The generated narrative text.
        input_data: The original SAR input used to generate the narrative (for grounding checks).
        min_words: Minimum acceptable word count.
        max_words: Maximum acceptable word count.
        strict_grounding: If True, require that subject name and key amounts appear in narrative.

    Returns:
        NarrativeValidationResult with passed=False if any check fails, and list of checks.
    """
    result = NarrativeValidationResult(passed=True)
    text = (narrative or "").strip()
    lower = text.lower()

    # --- Structure ---
    if not text:
        result.add("narrative_non_empty", False, "Narrative is empty")
    else:
        result.add("narrative_non_empty", True)

    # Single paragraph / no raw JSON in body (e.g. leaked {"narrative": "..."} wrapper)
    if text:
        has_json = bool(re.search(r'^\s*\{\s*"narrative"\s*:', text))
        result.add("no_json_in_body", not has_json, "Narrative should not contain raw JSON wrapper" if has_json else "")

    word_count = len(text.split()) if text else 0
    if text:
        result.add(
            "word_count",
            min_words <= word_count <= max_words,
            f"Word count {word_count} outside range [{min_words}, {max_words}]" if (word_count < min_words or word_count > max_words) else "",
        )

    # --- Tone: forbidden phrases ---
    if text:
        found_forbidden = []
        for pat in FORBIDDEN_PHRASES:
            if re.search(pat, lower, re.IGNORECASE):
                found_forbidden.append(pat)
        result.add(
            "no_forbidden_phrases",
            len(found_forbidden) == 0,
            f"Forbidden phrases found: {found_forbidden}" if found_forbidden else "",
        )

    # --- Content completeness: subject and dates from input ---
    if not input_data:
        result.add("subject_mentioned", True, "No input to check")
        result.add("date_mentioned", True, "No input to check")
    else:
        subject_name = None
        if isinstance(input_data.get("subject"), dict):
            subject_name = input_data["subject"].get("name") or input_data["subject"].get("subject_id")
        if subject_name and isinstance(subject_name, str):
            # Narrative should mention subject (name or "subject")
            mention = subject_name.lower() in lower if text else False
            mention = mention or ("subject" in lower and word_count > 30)
            result.add("subject_mentioned", mention, "Narrative should identify or refer to the subject")
        else:
            result.add("subject_mentioned", True, "No subject name in input")

        # Date or date range
        date_from = None
        date_to = None
        sai = input_data.get("SuspiciousActivityInformation") or {}
        if isinstance(sai.get("27_DateOrDateRange"), dict):
            date_from = sai["27_DateOrDateRange"].get("from")
            date_to = sai["27_DateOrDateRange"].get("to")
        if date_from or date_to:
            # Normalize to digits (e.g. 03/15/2024 -> 03152024 or 2024)
            date_str = (date_from or "") + " " + (date_to or "")
            digits = re.sub(r"\D", "", date_str)
            year_in = "2024" in digits or "2023" in digits or "2025" in digits
            year_in_narrative = "2024" in text or "2023" in text or "2025" in text
            result.add("date_mentioned", year_in_narrative or not digits, "Narrative should include date or date range of activity")
        else:
            result.add("date_mentioned", True, "No date in input")

        # Red flags / suspicious categories present in input should be reflected
        red_flags = []
        if isinstance(input_data.get("alert"), dict):
            red_flags = input_data["alert"].get("red_flags") or []
        for key in ("29_Structuring", "33_MoneyLaundering", "31_Fraud", "35_OtherSuspiciousActivities"):
            vals = sai.get(key)
            if isinstance(vals, list) and vals:
                red_flags.extend(str(v) for v in vals)
        if red_flags:
            # At least one concept should appear (e.g. structuring, money laundering, fraud)
            lower_flags = " ".join(red_flags).lower()
            has_concept = any(
                word in lower
                for word in ("structur", "money launder", "fraud", "suspicious", "unusual", "pattern", "transfer", "deposit", "wire", "ctr")
            )
            result.add("suspicious_patterns_reflected", has_concept, "Narrative should reflect suspicious activity types from input")
        else:
            result.add("suspicious_patterns_reflected", True, "No red flags in input")

    # --- Optional: key amounts from input appear in narrative ---
    if strict_grounding and input_data and text:
        amount_usd = None
        sai = input_data.get("SuspiciousActivityInformation") or {}
        if isinstance(sai.get("26_AmountInvolved"), dict):
            amount_usd = sai["26_AmountInvolved"].get("amount_usd")
        if amount_usd is not None:
            amount_str = str(int(amount_usd))
            result.add("amount_grounded", amount_str in text, f"Total amount {amount_usd} from input should appear in narrative")
        else:
            result.add("amount_grounded", True, "No amount in input")

    return result
