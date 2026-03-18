# Agentic AI Compliance Reporting System

## Introduction / Purpose
Financial institutions face high operational burden in Anti-Money Laundering (AML) and Bank Secrecy Act (BSA) workflows, especially for Suspicious Activity Report (SAR) and Currency Transaction Report (CTR) preparation. Manual report preparation is slow, error-prone, and difficult to audit at scale.

This project presents a production-oriented multi-agent compliance system that automates the end-to-end reporting pipeline: case intake, report-type routing, data aggregation, narrative generation (when required), validation, and PDF filing. The objective is to reduce analyst workload while preserving traceability, data quality, and regulatory alignment.


## Methods
### System Design
The system is implemented as a service-oriented architecture with:
- **FastAPI backend** for workflow orchestration and API exposure.
- **CrewAI-based agent orchestration** for staged report processing.
- **Streamlit frontend** for analyst-facing submission, tracking, and download workflows.
- **Supabase/PostgreSQL** for job lifecycle persistence and operational records.
- **Weaviate** for semantic retrieval of compliance knowledge.
- **Redis** for low-latency caching.

### Agent Pipeline
The active runtime path follows:
1. **Agent 1 (Router):** Classifies filing requirement (`SAR`, `CTR`, or both).
2. **Agent 3 (Aggregator):** Maps case input into schema-conformant report structures.
3. **Agent 4 (Narrative Generator):** Generates SAR narrative only when narrative is required.
4. **Agent 5 (Validator):** Applies rule-based compliance and quality checks.
5. **Agent 6 (Filer):** Produces final PDF output from report payloads and templates.

(Researcher agent is intentionally bypassed in the current operational path.)

### Knowledge and Rule Retrieval
- Report metadata, schema references, and filing guidance are integrated from the knowledge layer.
- Validation rules are executed through the teammate-provided validator package integrated into the pipeline.
- Workflow state is stored and exposed through job status APIs for real-time UI tracking.

### Interfaces and Endpoints
Key API endpoints include:
- `POST /api/v1/reports/submit`
- `GET /api/v1/reports/{job_id}/status`
- `GET /api/v1/reports/{job_id}/download`
- `GET /api/v1/dashboard/metrics`
- `GET /health` and `GET /health/lite`

### Operational Reliability Controls
- Timeout-bounded backend health checks for DB, Weaviate, and Redis.
- Staged progress updates in orchestration.
- Failure surfacing via status/error fields for analyst review.
- Deterministic filer stage to reduce non-deterministic output variability.

---

## Results
### Functional Outcomes
The system demonstrates end-to-end workflow execution for compliance case processing:
- Case submissions are accepted and persisted with job IDs.
- Multi-stage orchestration updates status (`submitted`, `processing`, `completed`/`failed`) and progress.
- Aggregator output is available per report type (`aggregator_by_type`) for auditability.
- Narrative generation is conditionally executed based on report requirements.
- Validator output is passed downstream into filing decisions.
- Filed reports are generated as downloadable PDFs.

### Product and UX Outcomes
- The Streamlit interface supports operational workflows for compliance officers: submit, monitor, review, and download.
- Health monitoring now supports both full dependency checks and lightweight liveness checks.
- Pipeline observability is improved through structured stage transitions and status reporting.

### Current Constraints Observed
- External service availability (notably DNS/network reachability to managed Weaviate endpoints) directly impacts knowledge-service health.
- Environment consistency (correct project path, virtual environment, and endpoint configuration) is required to avoid false-negative UI health states.

---

## Conclusions
This project delivers a practical, production-style architecture for automating AML/BSA report preparation with agentic orchestration. The implemented pipeline provides:
- A modular division of responsibilities across agents.
- Conditional narrative generation and validation-aware filing behavior.
- Persistent, API-accessible workflow state suitable for operational monitoring.
- A user-facing interface that abstracts backend complexity for analyst use.

From an academic and engineering perspective, the system validates that multi-agent orchestration can be applied to regulated-document workflows when combined with strong schema controls, explicit validation stages, and robust observability.

Future work should focus on:
- Expanded validation rule coverage and scoring calibration.
- Improved resilience to external knowledge-service outages.
- Formal benchmark evaluation (latency, precision/recall for routing, and filing accuracy by field).

---

## Acknowledgments
- Teammate contributions to the Narrative Generator and Validator components.
- Open-source tooling ecosystems used in this project (FastAPI, Streamlit, CrewAI, Weaviate, Redis, SQLAlchemy).

---

## References
1. Financial Crimes Enforcement Network (FinCEN). Suspicious Activity Report (SAR) requirements and guidance.
2. Financial Crimes Enforcement Network (FinCEN). Currency Transaction Report (CTR) requirements and guidance.
3. FastAPI Documentation. https://fastapi.tiangolo.com/
4. Streamlit Documentation. https://docs.streamlit.io/
5. CrewAI Documentation. https://docs.crewai.com/
6. Weaviate Documentation. https://weaviate.io/developers/weaviate
7. Supabase Documentation. https://supabase.com/docs
8. SQLAlchemy Documentation. https://docs.sqlalchemy.org/
9. Redis Documentation. https://redis.io/docs/
