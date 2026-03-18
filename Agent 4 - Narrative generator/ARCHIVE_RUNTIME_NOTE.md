# Runtime Note

This folder contains teammate reference implementations and development artifacts.

## Current production wiring

- Narrative generation runtime: `backend/agents/narrative_agent.py`
- Validator runtime adapter: `backend/agents/validator_agent.py` (loads validator `utils/*` + `data/validation_data/*` from this folder)

## Duplicate package roots

These two directories are duplicates and are not imported by the main pipeline runtime:

- `Agent 4 - Narrative generator/narrative_agent/`
- `Agent 4 - Narrative generator/src/narrative_agent/`

Keep them as reference only unless you explicitly rewire imports.
