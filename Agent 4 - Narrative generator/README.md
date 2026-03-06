## SAR Narrative Generator Agent

A **CrewAI** agent that generates the mandatory narrative section for Suspicious Activity Reports (SAR) and other report types from structured JSON input. The agent is designed to **not hallucinate**: it uses only the provided input data and follows narrative instructions and examples pulled from a Supabase-hosted knowledge base, with local fallbacks.

### Features

- **Input**: JSON with case, subject, institution, alert, SuspiciousActivityInformation (FinCEN-style fields), and transactions.
- **Output**: Same structure as input with one new field `narrative` added (input JSON + `{"narrative": "..."}`).
- **CrewAI**: One agent (SAR Narrative Writer) with one task; uses OpenAI `gpt-4o-mini` (small model).
- **Supabase knowledge base**: Uses the `report_types` and `narrative_examples` tables to load narrative instructions, schema-based guidance, and high-quality example narratives by `report_type_code` (e.g., `SAR`).
- **Local fallbacks**: If Supabase is not configured or unavailable, falls back to built-in effectiveness guidelines and few-shot examples.
- **Tests**: Pytest for schemas and agent (mocked so no API key needed for unit tests).
- **Demo**: Jupyter notebook showing full flow from input JSON to generated narrative.

### Setup

```bash
cd narrative_agent
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Set your OpenAI and Supabase environment variables:

```bash
export OPENAI_API_KEY=sk-...
export SUPABASE_URL="https://ggxnbctgyiitfwxharjt.supabase.co"
export SUPABASE_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

The Supabase URL and anon key must have read access to:

- `report_types` (including `narrative_instructions`, `json_schema`, `validation_rules`, `pdf_template_path`, and `pdf_field_mapping`)
- `narrative_examples` (including `summary`, `narrative_text`, `effectiveness_notes`, and `example_order`)

### Running the agent

From the project root, with `src` on the Python path:

```bash
cd narrative_agent
PYTHONPATH=src python -c "
from narrative_agent import generate_narrative
import json
with open('examples/input_example.json') as f:
    data = json.load(f)
out = generate_narrative(data, report_type_code='SAR')
print(json.dumps(out, indent=2))
"
```

Or use the class:

```python
from narrative_agent import NarrativeGeneratorCrew

crew = NarrativeGeneratorCrew(verbose=True)
result = crew.kickoff(inputs=<your_sar_input_dict>)
# result is input JSON + {'narrative': '...'}
```

Internally, the agent will:

- Call Supabase `report_types` for the given `report_type_code` to fetch `narrative_instructions` and any schema-based narrative guidance (e.g., Part V guidelines).
- Call Supabase `narrative_examples` for the same `report_type_code` to retrieve example narratives and effectiveness notes, which are incorporated into the prompt.
- Fall back to local examples and guidelines if the knowledge base cannot be reached or is not configured.

### Running tests

```bash
cd narrative_agent
PYTHONPATH=src pytest tests/ -v
```

Tests exercise schema validation and the agent orchestration. Knowledge base failures (e.g., missing `SUPABASE_URL` / `SUPABASE_ANON_KEY`) automatically fall back to local examples so tests remain deterministic.

### Jupyter notebook demo

From the project root:

```bash
cd narrative_agent
PYTHONPATH=src jupyter notebook notebooks/sar_narrative_demo.ipynb
```

Run all cells. The notebook loads the input example, shows the guidance used in the prompt, runs the agent, and displays the generated narrative in JSON.

### Project structure

```text
narrative_agent/
  requirements.txt
  README.md
  examples/
    input_example.json        # Example SAR input
    input_example2.json       # Additional example input
  src/
    narrative_agent/
      __init__.py
      agent.py                # CrewAI agent, task, crew; generate_narrative()
      schemas.py              # NarrativeOutput, validate_input/validate_output
      examples.py             # Local few-shot examples (fallback)
      narrative_reference.py  # Local effectiveness guidelines (fallback)
      knowledge_base.py       # Supabase client for report_types and narrative_examples
  tests/
    test_schemas.py
    test_agent.py
  notebooks/
    sar_narrative_demo.ipynb
```

### Verifying Supabase connectivity

To quickly verify that the knowledge base is wired correctly for SAR reports, run:

```bash
cd narrative_agent
PYTHONPATH=src python -c "
from narrative_agent.knowledge_base import fetch_report_type_config, fetch_narrative_examples
cfg = fetch_report_type_config('SAR')
examples = fetch_narrative_examples('SAR')
print('Report type:', cfg.report_type_code, '-', cfg.display_name)
print('Instructions snippet:', (cfg.narrative_instructions or '')[:200])
print('Loaded examples:', len(examples))
"
```

If this succeeds, the narrative agent will automatically use the Supabase-hosted instructions and examples when generating narratives.

### License

Internal use / Capstone project.
