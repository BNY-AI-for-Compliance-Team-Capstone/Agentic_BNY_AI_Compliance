"""
CrewAI Narrative Generator Agent for SAR reports.
Generates the narrative section from suspicious activity input without hallucination.
"""

import json
import re
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task

from narrative_agent.examples import get_few_shot_text
from narrative_agent.narrative_reference import get_reference_context
from narrative_agent.schemas import NarrativeOutput, validate_input


def _build_task_description(input_json: dict[str, Any]) -> str:
    """Build the task description with reference guidelines, few-shot examples, and current input."""
    reference = get_reference_context()
    few_shot = get_few_shot_text()
    input_str = json.dumps(input_json, indent=2)
    return f"""You are generating the mandatory narrative section for a Suspicious Activity Report (SAR). You must NOT hallucinate or invent any information. Use ONLY the data provided below.

{reference}

Few-shot examples (input -> narrative). Follow this style and use ONLY facts from the input:

{few_shot}

---

Current input (suspicious activity information) — use ONLY this data to write the narrative:

{input_str}

---

Generate exactly one narrative paragraph based solely on the current input above. Then output your response as a single JSON object with one key "narrative" whose value is that paragraph. No other keys. Example format: {{"narrative": "Your paragraph here."}}"""


def _parse_narrative_output(raw: str) -> NarrativeOutput:
    """Extract JSON from agent output and validate."""
    raw = raw.strip()
    # Try to find a JSON object in the output (in case of markdown or extra text)
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return NarrativeOutput(**data)


def create_crew(input_json: dict[str, Any], *, verbose: bool = True) -> Crew:
    """Create a CrewAI crew for one-shot narrative generation."""
    llm = LLM(
        model="openai/gpt-4o-mini",
        temperature=0.2,
        max_tokens=2000,
    )
    agent = Agent(
        role="SAR Narrative Writer",
        goal="Write accurate, factual SAR narratives using only the provided suspicious activity data. Never invent or assume facts.",
        backstory=(
            "You are a compliance analyst who drafts SAR narrative sections. "
            "You strictly use only the information given in the input. You never add names, dates, amounts, or events that are not explicitly in the data."
        ),
        llm=llm,
        verbose=verbose,
    )
    task = Task(
        description=_build_task_description(input_json),
        expected_output="A JSON object with a single key 'narrative' containing the SAR narrative paragraph. No other text.",
        agent=agent,
    )
    return Crew(
        agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose
    )


def generate_narrative(
    input_data: dict[str, Any], *, verbose: bool = True
) -> dict[str, Any]:
    """
    Generate SAR narrative from suspicious activity input.

    Args:
        input_data: Full SAR input JSON (case_id, subject, alert, SuspiciousActivityInformation, transactions, etc.).
        verbose: Whether to print CrewAI execution logs.

    Returns:
        Same structure as input with one additional key "narrative" (str). The returned dict
        contains all keys from input_data plus "narrative".
    """
    validate_input(input_data)
    crew = create_crew(input_data, verbose=verbose)
    result = crew.kickoff()
    # CrewAI returns CrewOutput; get last task's raw output
    raw_output = str(result)
    if hasattr(result, "tasks_output") and result.tasks_output:
        last = result.tasks_output[-1]
        raw_output = getattr(last, "raw", str(last))
    elif hasattr(result, "raw"):
        raw_output = result.raw
    parsed = _parse_narrative_output(raw_output)
    # Return exact same structure as input with one new field "narrative" (shallow copy + add key)
    out = dict(input_data)
    out["narrative"] = parsed.narrative
    return out


class NarrativeGeneratorCrew:
    """
    Convenience class to run the narrative generator with optional custom LLM.
    """

    def __init__(self, *, verbose: bool = True):
        self.verbose = verbose

    def kickoff(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run the crew and return the narrative output. inputs = SAR input JSON."""
        return generate_narrative(inputs, verbose=self.verbose)
