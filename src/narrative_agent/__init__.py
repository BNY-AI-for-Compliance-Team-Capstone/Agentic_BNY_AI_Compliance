"""SAR Narrative Generator Agent - CrewAI agent for suspicious activity report narratives."""

from narrative_agent.agent import NarrativeGeneratorCrew, generate_narrative
from narrative_agent.narrative_validation import (
    NarrativeValidationResult,
    ValidationCheck,
    validate_narrative,
)

__all__ = [
    "NarrativeGeneratorCrew",
    "generate_narrative",
    "validate_narrative",
    "NarrativeValidationResult",
    "ValidationCheck",
]
