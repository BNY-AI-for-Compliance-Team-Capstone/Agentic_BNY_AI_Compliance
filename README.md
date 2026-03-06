# Agentic_BNY_AI_Compliance

Production-style multi-agent compliance reporting system for SAR/CTR workflows.

This repository contains:
- FastAPI backend (`backend/`) with CrewAI orchestration
- Streamlit UI (`streamlit_app/`) for analyst workflows
- Knowledge layer using Supabase + Weaviate + Redis
- PDF filing tools for SAR/CTR templates

---

## Table of Contents

1. [What the System Does](#what-the-system-does)
2. [Current Agent Workflow](#current-agent-workflow)
3. [Architecture and Data Stores](#architecture-and-data-stores)
4. [Repository Layout](#repository-layout)
5. [Prerequisites](#prerequisites)
6. [Environment Variables](#environment-variables)
7. [Local Setup (Step-by-Step)](#local-setup-step-by-step)
8. [Initialize and Seed Knowledge Base](#initialize-and-seed-knowledge-base)
9. [Run Backend and Frontend](#run-backend-and-frontend)
10. [How to Test End-to-End](#how-to-test-end-to-end)
11. [UI Usage Guide](#ui-usage-guide)
12. [API Reference (Current)](#api-reference-current)
13. [PDF Filing Behavior and Field Mapping Notes](#pdf-filing-behavior-and-field-mapping-notes)
14. [Troubleshooting](#troubleshooting)
15. [Operational Notes for Teammates](#operational-notes-for-teammates)
16. [Security Notes](#security-notes)

---

## What the System Does

Given a transaction case (JSON or text input converted to JSON), the system:
1. Classifies filing type (`SAR`, `CTR`, `BOTH`, or none)
2. Aggregates and maps case fields into report-ready structures
3. Generates SAR narrative only when required
4. Validates quality/compliance (or bypasses in testing mode)
5. Fills PDF templates and returns downloadable report(s)

Output artifacts include:
- Job status and progress per stage
- Aggregated case payload(s)
- Narrative payload (SAR path)
- Validation outcome
- Final filed PDF path(s)

---

## Current Agent Workflow

### Active orchestration path (current runtime)

- Agent 1: **Router** (CrewAI)
- Agent 2: **Researcher** is intentionally skipped
- Agent 3: **Aggregator** (schema-aware mapper, Supabase-backed schema lookup)
- Agent 4: **Narrative** (only when `narrative_required=true`)
- Agent 5: **Validator** (CrewAI), or bypassed with `SKIP_VALIDATOR_FOR_TESTING=true`
- Agent 6: **Filer** (deterministic PDF filling)

### Execution flow

1. `POST /api/v1/reports/submit`
2. Job record is created in DB (`job_status` table)
3. Background task starts Crew workflow
4. Router classifies report types
5. Aggregator maps per report type (`aggregator_by_type`)
6. Narrative runs only for SAR when required
7. Validator runs unless bypassed
8. Filer generates PDF(s) on approved path
9. Job status is updated (`processing` -> `completed`/`failed`)

---

## Architecture and Data Stores

### Backend
- FastAPI (`backend/api/main.py`, `backend/api/routes.py`)
- Crew orchestration (`backend/orchestration/crew.py`)
- Agents (`backend/agents/*.py`)
- PDF tools and field mapping (`backend/tools/pdf_tools.py`, `backend/tools/field_mapper.py`)

### Data services

- **Supabase/Postgres (SQLAlchemy client in `supabase_client.py`)**
  - Job lifecycle (`job_status`)
  - Audit logs (`audit_log`)
  - Optional schema/rules/mappings persistence tables

- **Supabase REST (via `SUPABASE_URL` + `SUPABASE_ANON_KEY`)**
  - `report_types` table (schema/rules/mapping/narrative config)
  - `narrative_examples` table

- **Weaviate**
  - semantic retrieval for regulations/narratives/definitions

- **Redis**
  - cache for schema/rules/mappings/lookups

---

## Repository Layout

```text
backend/
  agents/
  api/
  config/
  knowledge_base/
  orchestration/
  tools/

streamlit_app/
  app.py
  pages/
  components/
  styles/
  utils/

knowledge_base/
  schemas/
  regulations/
  narratives/
  documents/pdf_templates/

scripts/
  preflight.py
  init_weaviate.py
  seed_kb.py
  e2e_submit_check.py
  test_pdf_filer.py
  test_ctr_filer.py
  test_crew.py
```

---

## Prerequisites

- Python 3.11+ (3.12 tested)
- `pip`
- Local Redis (unless using remote Redis)
- Weaviate (local or cloud)
- Postgres-compatible DB for job storage (local Postgres or Supabase DB)
- OpenAI API key for embedding/LLM operations

---

## Environment Variables

Use `.env.example` as base.

### Required in practice

- `OPENAI_API_KEY`
- `WEAVIATE_URL`
- `REDIS_URL`
- A working DB DSN path via one of:
  - `SUPABASE_DB_URL`
  - or `SUPABASE_DB_HOST` + `SUPABASE_DB_PASSWORD` (+ related components)
  - or fallback `DATABASE_URL`

### Required for Supabase REST-backed KB reads

- `SUPABASE_URL` (HTTP URL, e.g. `https://<project_ref>.supabase.co`)
- `SUPABASE_ANON_KEY`

### Important URL distinction

- `SUPABASE_URL` must be **HTTP(S)** for REST API access.
- DB connection must be **PostgreSQL DSN** (`postgresql://...`) via DB vars.
- Do not put HTTP URL into DB DSN fields.

### Validator bypass toggle

- `SKIP_VALIDATOR_FOR_TESTING=true`
  - bypasses Agent 5
  - auto-approves for filing
  - useful for integration tests and PDF mapping iteration

---

## Local Setup (Step-by-Step)

From repo root:

```bash
cd Agentic_BNY_AI_Compliance
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

If you use Conda, deactivate first to avoid interpreter/package mismatch:

```bash
conda deactivate 2>/dev/null || true
source .venv/bin/activate
which python
```

Expected `which python` -> `.venv/bin/python`

---

## Initialize and Seed Knowledge Base

### 1) Preflight checks

```bash
source .venv/bin/activate
set -a; source .env; set +a
python scripts/preflight.py
```

### 2) Init Weaviate classes

```bash
python scripts/init_weaviate.py
```

### 3) Seed KB

```bash
python scripts/seed_kb.py
```

Notes:
- If no real DB DSN is configured, `seed_kb.py` enters REST-only mode and skips SQL table seeding.
- Weaviate seeding still runs.

---

## Run Backend and Frontend

### Backend

```bash
source .venv/bin/activate
uvicorn backend.api.main:app --reload --port 8001
```

Health check:

```bash
curl http://127.0.0.1:8001/health
```

Expected shape:

```json
{"status":"healthy","services":{"database":true,"weaviate":true,"redis":true}}
```

### Frontend (Streamlit)

Run in a separate terminal:

```bash
source .venv/bin/activate
streamlit run streamlit_app/app.py --server.port 8501
```

Open:
- `http://localhost:8501`

Current sidebar navigation intentionally shows:
- Home
- Dashboard
- Submit Case
- Case Management
- Settings

---

## How to Test End-to-End

### A) API E2E smoke test

```bash
source .venv/bin/activate
python scripts/e2e_submit_check.py
```

This submits:
- one SAR-only case (expects narrative path)
- one CTR-only case (expects narrative skip)

Expected final line:
- `PASS: End-to-end checks succeeded.`

### B) Direct PDF filer tests

```bash
source .venv/bin/activate
python scripts/test_pdf_filer.py --input data/CASE-2024-677021.json --report-type SAR
python scripts/test_pdf_filer.py --input data/ctr_test_case.json --report-type CTR
```

### C) Full orchestration test from script

```bash
source .venv/bin/activate
python scripts/test_crew.py --input data/CASE-2024-677021.json
```

---

## UI Usage Guide

## Home
- KPI cards
- recent activity
- primary action: submit new case

## Submit Case
Input methods:
- **Text Input**
  - supports plain text extraction (amount/date heuristics)
  - also supports pasted JSON (raw JSON, fenced JSON, embedded JSON)
- **Upload JSON**
  - recommended for exact field fidelity
- **Manual Entry**
  - builds structured payload from form fields
- **Batch Upload**
  - placeholder currently
- **Direct PDF Filing**
  - bypasses agent pipeline and directly fills PDF from JSON path

## Case Management
- list tracked cases
- stage timeline
- case details
- download artifacts when completed

## Dashboard
- overview metrics and trend charts

## Settings
- API base URL, timeout, retry
- user profile and display options

---

## API Reference (Current)

Router prefix: `/api/v1`

### Implemented endpoints

- `POST /api/v1/reports/submit`
- `GET /api/v1/reports/{job_id}/status`
- `GET /api/v1/reports/{job_id}/download`
- `POST /api/v1/reports/file-direct`
- `GET /api/v1/kb/search`

Global health endpoint:
- `GET /health`

Note:
- Some UI client methods include fallback behavior when optional endpoints are absent (`/cases/*`, `/reports/list`, `/dashboard/metrics`).

---

## PDF Filing Behavior and Field Mapping Notes

### Templates

SAR filer auto-selects best template match between:
- `knowledge_base/documents/pdf_templates/fincen_sar_form_acroform.pdf`
- `knowledge_base/documents/pdf_templates/sar_report.pdf`

CTR template:
- `knowledge_base/documents/pdf_templates/ctr_report.pdf`

### Key SAR mapping details

- Field 34 (`Total dollar amount involved`) is dollar-only in legacy form:
  - cents are not represented in the numeric boxes
  - mapper fills both `item34` and `item34-1..item34-11`

- Entity name handling:
  - names like `LLC`, `INC`, `CORP` are treated as entity-style mapping even if `subject.type` is mislabeled

- Address fallback:
  - if address missing, mapper backfills from available `city/state/zip`
  - transaction location is used as fallback source when needed

- Item 35 summary characterization:
  - mapping uses conservative category/keyword logic
  - unmatched categories are preserved in `item35s-1` (other text)

### PDF appearance and duplication fixes

- `NeedAppearances` set to improve viewer rendering
- duplicate-page writes were removed (no double page sets)

---

## Troubleshooting

### 1) `zsh: command not found: app.py`
Use Streamlit runner, not direct python script execution:

```bash
streamlit run streamlit_app/app.py --server.port 8501
```

### 2) `ModuleNotFoundError: No module named 'streamlit_app'`
Run from repository root:

```bash
cd Agentic_BNY_AI_Compliance
streamlit run streamlit_app/app.py --server.port 8501
```

### 3) UUID parsing errors in status endpoint
Do not pass placeholders like `<REAL_UUID>` literally.
Use the actual `job_id` returned from submit.

### 4) `jq: Unknown option --argfile`
Use Python-based payload construction or a compatible jq invocation.

### 5) DB connection errors (`password authentication failed`, `connection refused`)
Validate DB env vars:
- host/user/password/port/db
- DSN format
- whether local or Supabase DB is reachable from your machine

### 6) Supabase REST 400 on `report_types`
Check table schema and column names used by code:
- `report_type`
- `json_schema`
- `narrative_required`
- `narrative_instructions`
- `validation_rules`
- `pdf_template_path`
- `pdf_field_mapping`

### 7) UI styling looks inconsistent across pages
After updates:
- restart Streamlit
- hard refresh browser (`Cmd+Shift+R`)

### 8) Port already in use

```bash
lsof -ti tcp:8001 -sTCP:LISTEN
kill -9 <pid>
```

### 9) Weaviate URL errors
Always include scheme:
- `http://localhost:8080` (local)
- `https://<cluster>.weaviate.cloud` (cloud)

---

## Operational Notes for Teammates

### Standard daily run

1. Activate venv and load env:

```bash
source .venv/bin/activate
set -a; source .env; set +a
```

2. Start backend:

```bash
uvicorn backend.api.main:app --reload --port 8001
```

3. Start frontend:

```bash
streamlit run streamlit_app/app.py --server.port 8501
```

4. Test with Upload JSON first for deterministic mapping.

### Recommended testing mode during integration

Set in `.env`:

```env
SKIP_VALIDATOR_FOR_TESTING=true
```

Use this while validating Router -> Aggregator -> Narrative -> Filer behavior.

---

## Security Notes

- Do not commit `.env` or credentials.
- Rotate API keys if exposed.
- Use least-privilege Supabase keys in shared environments.
- Audit and sanitize case data before sharing externally.

---

## Quick Start Commands (Copy/Paste)

```bash
cd Agentic_BNY_AI_Compliance
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a; source .env; set +a
python scripts/preflight.py
python scripts/init_weaviate.py
python scripts/seed_kb.py
uvicorn backend.api.main:app --reload --port 8001
```

New terminal:

```bash
cd Agentic_BNY_AI_Compliance
source .venv/bin/activate
streamlit run streamlit_app/app.py --server.port 8501
```

