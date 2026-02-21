import os
import json
from dotenv import load_dotenv
from crewai import Crew, Process

from src.agents import get_hongyi_validator_agent
from src.tasks import create_validation_task
from src.rule_engine import RuleEngine

load_dotenv()

def load_json(filepath: str):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    print("Initializing Agent 5: Validator - Hongyi...\n")
    
    rules_kb = load_json('data/validation_rules.json')
    legal_kb = json.dumps(load_json('data/legal_requirements.json'), indent=2)
    cases = load_json('data/sample_cases.json')

    agent = get_hongyi_validator_agent()

    for case in cases:
        case_id = case.get("case_id", "UNKNOWN")
        print(f"--- Processing {case_id} ---")
        
        # 1. Run Deterministic Python Engine
        score, missing = RuleEngine.calculate_completeness(case)
        issues = RuleEngine.run_rules(case, rules_kb)
        
        # 2. Hand off to LLM Evaluator for Semantic/Legal Checks
        task = create_validation_task(
            agent=agent,
            case_dict=case,
            legal_kb=legal_kb,
            det_score=score,
            det_missing=missing,
            det_issues=issues
        )

        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False
        )

        result = crew.kickoff()
        
        print(f"\n[FINAL REPORT: {case_id}]")
        print(result.json(indent=4))
        print("="*60 + "\n")

if __name__ == "__main__":
    main()