# SAR Narrative Generator Agent

A **CrewAI** agent that generates the mandatory narrative section for Suspicious Activity Reports (SAR) from structured JSON input. The agent is designed to **not hallucinate**: it uses only the provided input data and follows few-shot examples and effectiveness guidelines.

## Features

- **Input:** JSON with case, subject, institution, alert, SuspiciousActivityInformation (FinCEN-style fields), and transactions.
- **Output:** Same structure as input with one new field `narrative` added (input JSON + `{"narrative": "..."}`).
- **CrewAI:** One agent (SAR Narrative Writer) with one task; uses OpenAI `gpt-4o-mini` (small model).
- **Few-shot examples:** Built-in examples (input snippet → narrative) used in the agent prompt.
- **Reference narratives:** Effectiveness guidelines and example explanations so the agent aligns with SAR best practices.
- **Tests:** Pytest for schemas and agent (mocked so no API key needed for unit tests).
- **Demo:** Jupyter notebook showing full flow from input JSON to generated narrative.

## Setup

```bash
cd narrative_agent
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-...
# or add OPENAI_API_KEY=sk-... to a .env file in the project root
```

## Running the agent

From the project root, with `src` on the Python path:

```bash
cd narrative_agent
PYTHONPATH=src python -c "
from narrative_agent import generate_narrative
import json
with open('examples/input_example.json') as f:
    data = json.load(f)
out = generate_narrative(data)
print(json.dumps(out, indent=2))
"
```

Or use the class:

```python
from narrative_agent import NarrativeGeneratorCrew
crew = NarrativeGeneratorCrew(verbose=True)
result = crew.kickoff(inputs=<your_sar_input_dict>)
# result is {"narrative": "..."}
```

## Running tests

```bash
cd narrative_agent
PYTHONPATH=src pytest tests/ -v
```

## Jupyter notebook demo

From the project root:

```bash
cd narrative_agent
PYTHONPATH=src jupyter notebook notebooks/sar_narrative_demo.ipynb
```

Run all cells. The notebook loads the input example, shows few-shot examples and reference guidelines, runs the agent, and displays the generated narrative in JSON.

## Project structure

```
narrative_agent/
  requirements.txt
  README.md
  examples/
    input_example.json       # Example SAR input
  src/
    narrative_agent/
      __init__.py
      agent.py               # CrewAI agent, task, crew; generate_narrative()
      schemas.py             # NarrativeOutput, validate_input/validate_output
      examples.py            # Few-shot examples (input + narrative)
      narrative_reference.py # Effectiveness guidelines and reference narratives
  tests/
    test_schemas.py
    test_agent.py
  notebooks/
    sar_narrative_demo.ipynb
```

## License

Internal use / Capstone project.
