"""Tests for the Narrative Generator Agent."""

from unittest.mock import MagicMock, patch

import pytest

from narrative_agent.agent import _parse_narrative_output, create_crew, generate_narrative
from narrative_agent.schemas import validate_input


def test_parse_narrative_output_pure_json():
    raw = '{"narrative": "The subject made multiple cash deposits."}'
    out = _parse_narrative_output(raw)
    assert out.narrative == "The subject made multiple cash deposits."


def test_parse_narrative_output_with_markdown():
    raw = 'Here is the output:\n```json\n{"narrative": "A factual SAR narrative."}\n```'
    out = _parse_narrative_output(raw)
    assert out.narrative == "A factual SAR narrative."


def test_parse_narrative_output_extra_text():
    raw = 'Some prefix {"narrative": "Only this counts."} trailing'
    out = _parse_narrative_output(raw)
    assert out.narrative == "Only this counts."


@patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-dummy"}, clear=False)
def test_create_crew_returns_crew():
    input_json = {
        "case_id": "C-1",
        "subject": {"name": "Test"},
        "SuspiciousActivityInformation": {},
    }
    crew = create_crew(input_json, verbose=False)
    assert crew is not None
    assert len(crew.agents) == 1
    assert len(crew.tasks) == 1


@patch("narrative_agent.agent.create_crew")
def test_generate_narrative_mocked_crew(mock_create_crew):
    input_data = {
        "case_id": "CASE-2024-677021",
        "subject": {"subject_id": "C-94926", "name": "Global Trade Corp", "type": "Individual"},
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": 25500.0},
            "27_DateOrDateRange": {"from": "03/15/2024", "to": "03/22/2024"},
        },
    }
    mock_result = MagicMock()
    mock_result.tasks_output = [MagicMock(raw='{"narrative": "Generated narrative for Global Trade Corp."}')]
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = mock_result
    mock_create_crew.return_value = mock_crew

    result = generate_narrative(input_data, verbose=False)

    # Output is input + narrative (same format as input, one new field)
    assert result["narrative"] == "Generated narrative for Global Trade Corp."
    assert result["case_id"] == input_data["case_id"]
    assert result["subject"] == input_data["subject"]
    assert set(result.keys()) == set(input_data.keys()) | {"narrative"}
    mock_create_crew.assert_called_once_with(input_data, verbose=False)
    mock_crew.kickoff.assert_called_once()


def test_generate_narrative_validates_input():
    with pytest.raises(ValueError, match="required keys"):
        generate_narrative({"case_id": "C-1"})  # missing subject and SuspiciousActivityInformation
