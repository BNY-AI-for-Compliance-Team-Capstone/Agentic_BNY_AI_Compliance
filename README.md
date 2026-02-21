# Agent 5: Validator (Hongyi Quality Gate)

An enterprise-grade validation agent designed for an "AI for Compliance" workflow. It leverages a Hybrid Architecture (Python Deterministic Engine + CrewAI Semantic Evaluator) to ensure uncompromising data accuracy and regulatory adherence.

## Architecture Overview
According to the Figure 1 requirements, validation requires both structural compliance and narrative intelligence:
1. **RuleEngine (Python):** Handles exact completeness scoring and `validation_rules.json` logical assertions (e.g., Null checks, amount thresholds).
2. **CrewAI Evaluator (Gemini 1.5 Pro):** Ingests the deterministic results, maps the narrative against `legal_requirements.json`, and cross-references JSON entities (transactions) against the narrative text.

## Setup Instructions

1. **Environment Initialization:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt

2. **API Configuration:**
Place your Gemini API key in the `.env` file:
`GEMINI_API_KEY=your_key_here`
3. **Execution:**
```bash
python main.py