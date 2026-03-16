# Router Agent

Entry-point agent for the BNY AI Compliance multi-agent pipeline. It classifies the compliance report type from the user's input (natural language or JSON) and validates that required form fields are present using the Supabase Knowledge Base.

## Standalone Streamlit frontend (all in `router_agent/`)

A complete working frontend is included and connects to the existing backend. No changes outside this folder.

1. **Run the backend** (from repo root): `uvicorn backend.api.main:app --reload --port 8001`
2. **Run the Router Agent app** (from repo root): `streamlit run router_agent/app.py --server.port 8502`
3. Open **http://localhost:8502**. Use **Text input** or **JSON input** → **Classify & validate** → review result and missing fields → **Submit to full pipeline** to send the case to the backend. Set API base URL in the sidebar (default `http://localhost:8001`).

## Responsibilities

1. **Classify report type** – Uses an LLM (OpenAI GPT-4.1 mini) via CrewAI to determine whether the user needs SAR, CTR, Sanctions, or BOTH.
2. **Check KB** – Queries the Knowledge Base (Supabase REST `report_types` or Postgres `report_schemas` table) to see if the report type exists and has a JSON schema.
3. **Validate input** – Maps user input to the report’s JSON schema and identifies any **missing required fields**.
4. **Prompt user** – Returns `missing_fields` and a `message` so the Streamlit UI can ask the compliance officer for the missing details.

## Usage

From Python (e.g. backend or Streamlit):

```python
from router_agent import run_router, RouterResult

# Natural language input
result = run_router("I need to file a SAR for a suspicious wire transfer.")
print(result.report_type)       # SAR
print(result.kb_status)        # EXISTS | MISSING
print(result.missing_fields)   # e.g. ["subject.name", "SuspiciousActivityInformation"]
print(result.message)         # Shown to user to request missing info

# Structured JSON input
result = run_router({"report_type": "SAR", "subject": {"name": "John"}, ...})
```

## Environment

Uses the same `.env` as the rest of the project:

- `OPENAI_API_KEY` – required for the router LLM (GPT-4.1 mini).
- **Supabase (your tables)** – When `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set, the router uses your Supabase **report_types** (filter by `report_type_code`) and **required_fields** (filter by `report_type_code` and `is_required=true`; uses `input_key` as the required path). Ensure `input_key` matches JSON paths in case payloads (e.g. `subject.name`).
- **Postgres fallback** – If Supabase REST is not set, the router uses backend KBManager (Postgres `report_schemas` or legacy REST).

No new env vars are introduced; add your keys to the repo’s existing `.env` (see root `.env.example`).

## Layout

- `app.py` – Standalone Streamlit app: input → router → result → submit to backend.
- `agent.py` – CrewAI agent and task for report-type classification.
- `kb_client.py` – KB access: when Supabase REST is enabled, uses `report_types` (report_type_code) and `required_fields` (input_key); otherwise uses backend KBManager.
- `supabase_rest.py` – Supabase REST client for your table structure (report_type_code, required_fields).
- `schema_validator.py` – Extracts required field paths from JSON schema and validates input.
- `run.py` – Orchestrates: classify → check KB → validate → return `RouterResult`.
- `config.py` – Model name, supported report types, and default API base URL (`COMPLIANCE_API_BASE_URL`).

## Cross-check with codebase (router_agent branch and others)

### Knowledge Base / Supabase / Postgres

- **Backend** (`backend/knowledge_base/kb_manager.py`): Two ways to get report schemas:
  - **Supabase REST**: If `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set, it reads from the **`report_types`** table (columns: `report_type`, `json_schema`, `narrative_instructions`, etc.) via REST.
  - **Postgres SQL**: Otherwise it uses **`SupabaseClient`** (SQLAlchemy) and the **`report_schemas`** table (`report_type`, `schema_json`). The connection uses `settings.get_database_url()`, which falls back to **`DATABASE_URL`** when Supabase-specific vars are not set.

- **Router agent**: When `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set, `router_agent/kb_client.py` uses **`router_agent/supabase_rest.py`** to read your **report_types** (by `report_type_code`) and **required_fields** (by `report_type_code`, using `input_key` for required paths). Otherwise it falls back to `KBManager()` (Postgres `report_schemas` or legacy REST).

- **Schema shape**: SQL path returns `schema_json` (the full schema dict, e.g. from `knowledge_base/schemas/sar_schema.json`: `report_type`, `definitions`, `input_payload_schema`). REST path returns the `json_schema` column. **`router_agent/schema_validator.py`** supports both: it uses `definitions`, `input_payload_schema`, and `required_fields` to derive required field paths.

### Seeding the database

- **`scripts/seed_kb.py`** creates tables and seeds SAR/CTR schemas only when **`settings.has_database_dsn()`** is true. That method currently checks **`SUPABASE_DB_URL`**, **`SUPABASE_DB_HOST` + `SUPABASE_DB_PASSWORD`**, or **`SUPABASE_URL`** as a Postgres DSN; it does **not** check **`DATABASE_URL`**.
- If your `.env` has **only `DATABASE_URL`** (and `POSTGRES_*`), `has_database_dsn()` is false and **seed_kb will skip the database step**. The app and router still connect to Postgres via `get_database_url()` (which uses `DATABASE_URL`), but **`report_schemas` will be empty** unless you seed it.
- **Workaround**: Either (1) set **`SUPABASE_DB_HOST=localhost`** and **`SUPABASE_DB_PASSWORD=Secur3Pass!`** in `.env` so `has_database_dsn()` is true and `python scripts/seed_kb.py` seeds the DB, or (2) create tables and insert SAR/CTR schemas yourself (e.g. run `SupabaseClient().create_tables()` then add rows matching `knowledge_base/schemas/sar_schema.json` and `ctr_schema.json`).

### Streamlit and backend

- **Main Streamlit app** (`streamlit_app/`): Uses `APIClient` to call **`POST /api/v1/reports/submit`** with `transaction_data`. No Supabase or SQL is called from the frontend; all KB access is in the backend.
- **Router agent app** (`router_agent/app.py`): Calls **`run_router()`** in process, then submits to the **same** **`POST /api/v1/reports/submit`** with the payload. Backend URL is configurable via sidebar or **`COMPLIANCE_API_BASE_URL`**. No changes to the main Streamlit app or backend are required for the router agent to work.

### Other branches (Narrative, Validator, etc.)

- Aggregator and narrative agents use **Supabase REST** when configured (e.g. `report_types` table). The router agent does not depend on those branches; it only needs **`KBManager.get_schema()`** and the backend’s **`reports/submit`** endpoint, which are present on the **router_agent** branch.

## Integration

This folder does not modify existing backend or Streamlit files. To plug it into the app:

- **API**: Add a route (e.g. `POST /api/v1/router/classify`) that calls `run_router(payload)` and returns `result.to_dict()`.
- **Streamlit**: On Submit Case, call `run_router(...)` (or the new API). If `result.missing_fields` is non-empty, show `result.message` and optionally a form for the missing fields before calling the full pipeline.
