"""Schemas and validators for narrative generation I/O."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NarrativeOutput(BaseModel):
    """Structured output contract for narrative generation."""

    narrative: str = Field(
        ...,
        description="Generated narrative text based strictly on provided input data.",
    )


def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    """Validate minimum required keys for narrative generation."""
    required = {"case_id", "subject", "SuspiciousActivityInformation"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Input missing required keys: {sorted(missing)}")
    return data


def validate_output(data: dict[str, Any]) -> NarrativeOutput:
    """Validate and parse narrative output payload."""
    return NarrativeOutput(**data)
