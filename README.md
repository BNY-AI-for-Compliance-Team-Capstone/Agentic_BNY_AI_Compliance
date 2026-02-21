# Agent 5: Validator - Hongyi (Quality Gate)

This repository contains the **Validator Agent** implementation for the **AI for Compliance** Capstone project. Acting as the final **Quality Gate**, this agent ensures that generated Suspicious Activity Reports (SARs) meet strict regulatory standards before human review.

---

## 🎯 Overview

The **Validator Agent (Hongyi)** implements a **Hybrid Validation Architecture**. By combining a deterministic Python-based Rule Engine with a semantic LLM Evaluator, the system mitigates hallucinations and ensures 100% adherence to legal requirements.

### Key Capabilities

* **Completeness Check**: Deterministically verifies that all required fields in the SAR JSON are populated.
* **Regulatory Compliance**: Semantic validation against **BSA, AML, and OFAC** laws.
* **Data Accuracy**: Cross-references narrative text against structured transaction data to identify inconsistencies.
* **Quality Metrics**: Analyzes narrative clarity and detail sufficiency using scoring algorithms.

---

## 📂 Project Structure

```text
validator_project/
├── data/
│   ├── sample_cases.json         # Raw SAR cases (e.g., Global Trade Corp)
│   ├── validation_rules.json     # KB: Logical assertions (SAR-C001, etc.)
│   └── legal_requirements.json   # KB: Regulatory framework definitions
├── src/
│   ├── __init__.py               # Package exposure logic
│   ├── models.py                 # Pydantic schemas (ValidatorOutput, Report)
│   ├── rule_engine.py            # Python logic for hard-rule validation
│   ├── agents.py                 # CrewAI agent definitions (Gemini API)
│   └── tasks.py                  # Semantic validation task configuration
├── main.py                       # Application entry point and orchestrator
├── requirements.txt              # Dependency list
├── .env                          # Local environment secrets (API Keys)
└── README.md                     # Project documentation

```

---

## 🛠 Tech Stack

* **Agent Framework**: CrewAI
* **Orchestration**: Python 3.10+
* **Core LLM**: Google Gemini 1.5 Pro (via Gemini API)
* **Validation Layer**: Pydantic v2
* **Environment**: Git Bash / MINGW64

---

## 📋 Installation & Setup

### 1. Clone and Navigate

```bash
git checkout validator
cd validator_project

```

### 2. Virtual Environment Setup

```bash
python -m venv venv
source venv/Scripts/activate  # For Git Bash on Windows
pip install -r requirements.txt

```

### 3. API Configuration

Create a `.env` file in the root directory:

```env
GEMINI_API_KEY=your_google_gemini_api_key_here

```

---

## 🏃 Running the Validator

To execute the validation pipeline on the sample cases (including Case-2024-677021, CASE-2025-380469, and CASE-2025-425659), run:

```bash
python main.py

```

### Process Logic

1. **Trigger**: Always runs as the final quality gate.
2. **Deterministic Pass**: The `RuleEngine` checks `validation_rules.json` for null values and threshold violations.
3. **Semantic Pass**: The **Hongyi Validator Agent** reviews the `narrative_text` against `legal_requirements.json`.
4. **Consolidation**: Scores are aggregated into a final `validation_report`.

---

## ⚖️ Knowledge Base (KB) Design

### Validation Rules (`validation_rules.json`)

Deterministic logic for field-level validation:

```json
{
  "rule_id": "SAR-C001",
  "report_type": "SAR",
  "severity": "critical",
  "rule_json": {
    "condition": "subject.last_name IS NOT NULL OR subject.ssn IS NOT NULL",
    "message": "Subject must have last name or SSN"
  }
}

```

### Legal Requirements (`legal_requirements.json`)

Guidance for LLM-based regulatory assessment:

```json
{
  "req_id": "LEG-BSA-001",
  "framework": "BSA (Bank Secrecy Act)",
  "evaluation_logic": "Verify narrative justifies suspicion of structuring."
}

```

---

## 📊 Output Specifications

The agent produces a structured JSON output via Pydantic:

| Field | Type | Description |
| --- | --- | --- |
| `status` | Enum | `APPROVED`, `NEEDS_REVIEW`, or `REJECTED` |
| `completeness_score` | Float | Percentage (0-100%) of required fields populated |
| `missing_fields` | List | Keys that failed the completeness check |
| `compliance_issues` | List | Regulatory or logic rule violations found |
| `recommendations` | List | Guidance for remediation |
| `approval_flag` | Boolean | Final binary decision for the pipeline |
