from crewai import Task
from src.models import ValidatorOutput
import json

def create_validation_task(agent, case_dict: dict, legal_kb: str, det_score: float, det_missing: list, det_issues: list) -> Task:
    
    prompt = f"""
    Execute the Final Quality Gate Validation for Case: {case_dict.get('case_id')}
    
    --- STEP 1: REVIEW DETERMINISTIC ENGINE OUTPUTS ---
    Completeness Score: {det_score}%
    Missing Fields: {det_missing}
    Rule Engine Violations: {det_issues}
    
    --- STEP 2: PERFORM SEMANTIC EVALUATION ---
    1. Regulatory compliance: Check the Narrative against these Legal Requirements: {legal_kb}
    2. Data accuracy: Verify that amounts, dates, and account numbers in the Narrative perfectly match the JSON structured data.
    3. Quality metrics: Assess narrative clarity, detail sufficiency, and logical flow.
    
    --- STEP 3: SYNTHESIS & SCORING ---
    Aggregate the deterministic issues with any semantic/data accuracy issues you find.
    
    Routing Logic:
    - If there are ANY Missing Fields, Rule Violations, or Data Mismatches: Status = 'NEEDS_REVIEW', approval_flag = false
    - If Completeness < 100%: Status = 'REJECTED' or 'NEEDS_REVIEW', approval_flag = false
    - If 100% compliant and accurate: Status = 'APPROVED', approval_flag = true
    
    Case Data:
    {json.dumps(case_dict, indent=2)}
    """

    return Task(
        description=prompt,
        expected_output="A structured JSON object adhering strictly to the ValidatorOutput schema.",
        agent=agent,
        output_pydantic=ValidatorOutput
    )